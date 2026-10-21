"""Corrections keep the original event and failed writes leave no partial state."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select

import app.player_state as player_state
from app.db.models import GameEvent, Outbox, PlayerState, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_corrections_and_failed_action_rollback(monkeypatch) -> None:
    async def run() -> None:
        user_ids: list[str] = []
        room_id: str | None = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            host, player, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(3)]
            try:
                for client, name in ((host, "Quản trò đính chính"),
                                     (player, "Người chơi đính chính"),
                                     (outsider, "Người ngoài đính chính")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Thử đính chính", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                assert (await post(player, "/api/rooms/join", {
                    "code": code, "accept_postgame_transcript": True})).status_code == 200
                # Two members are needed for random distribution.
                second = await stack.enter_async_context(httpx.AsyncClient(
                    transport=transport, base_url="http://test"))
                joined = await post(second, "/api/auth/guest", {"display_name": "Người thứ hai"})
                assert joined.status_code == 200
                user_ids.append(joined.json()["id"])
                assert (await post(second, "/api/rooms/join", {
                    "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{base}/roles", {
                    "name": "Dân", "description": "Thử nghiệm", "faction_label": "Làng",
                    "seer_result": "Thiện",
                    "role_kind": "passive", "quantity": 2})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Thử đính chính", "seat_limit": 2, "registration_open": False,
                }, headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200

                events_url = f"{base}/game/events"
                assert (await player.get(f"{events_url}/host")).status_code == 403
                assert (await outsider.get(f"{events_url}/public-corrections")).status_code == 403
                host_events = (await host.get(f"{events_url}/host")).json()
                start = next(event for event in host_events if event["kind"] == "game.started")
                assert start["visibility"] == "room"
                board_url = f"{base}/game/players"
                action_url = f"{board_url}/{user_ids[1]}/actions"
                async with session_factory()() as db:
                    before_count = await db.scalar(select(func.count(GameEvent.id)).where(
                        GameEvent.room_id == UUID(room_id)))
                    before_outbox = await db.scalar(select(func.count(Outbox.id)).join(
                        GameEvent, GameEvent.id == Outbox.event_id).where(
                        GameEvent.room_id == UUID(room_id)))
                    before_version = (await db.get(Room, UUID(room_id))).version

                original_append = player_state.append_event

                async def fail_append(*args, **kwargs):
                    raise RuntimeError("simulate append failure")

                monkeypatch.setattr(player_state, "append_event", fail_append)
                failed = await post(host, action_url, {
                    "client_request_id": str(uuid4()), "action": "kill", "reason": "Thử rollback"})
                assert failed.status_code == 500
                monkeypatch.setattr(player_state, "append_event", original_append)
                async with session_factory()() as db:
                    assert (await db.get(PlayerState, (UUID(room_id), UUID(user_ids[1])))).is_alive
                    assert (await db.get(Room, UUID(room_id))).version == before_version
                    assert await db.scalar(select(func.count(GameEvent.id)).where(
                        GameEvent.room_id == UUID(room_id))) == before_count
                    assert await db.scalar(select(func.count(Outbox.id)).join(
                        GameEvent, GameEvent.id == Outbox.event_id).where(
                        GameEvent.room_id == UUID(room_id))) == before_outbox

                killed = await post(host, action_url, {
                    "client_request_id": str(uuid4()), "action": "kill", "reason": "Thử đính chính"})
                assert killed.status_code == 200
                kill_event_id = killed.json()["event_id"]
                public_version_before = (await player.get(base)).json()["version"]
                private_body = {"client_request_id": str(uuid4()),
                                "source_event_id": kill_event_id,
                                "reason": "Quản trò ghi sai lý do", "corrected_text": "Lý do đúng là thử nghiệm"}
                assert (await post(player, f"{events_url}/corrections", private_body)).status_code == 403
                private = await post(host, f"{events_url}/corrections", private_body)
                assert private.status_code == 200 and private.json()["public_event_id"] is None
                assert (await post(host, f"{events_url}/corrections", private_body)).json() == {
                    **private.json(), "repeated": True}
                assert (await post(host, f"{events_url}/corrections", {
                    **private_body, "corrected_text": "Khác"})).status_code == 409
                assert (await player.get(f"{events_url}/public-corrections")).json() == []
                assert (await player.get(base)).json()["version"] == public_version_before

                public_body = {"client_request_id": str(uuid4()),
                               "source_event_id": start["id"],
                               "reason": "Lý do chỉ quản trò xem",
                               "corrected_text": "Ngày 0 bắt đầu sau khi kiểm tra lại role."}
                published = await post(host, f"{events_url}/corrections", public_body)
                assert published.status_code == 200 and published.json()["public_event_id"]
                assert (await player.get(base)).json()["version"] == public_version_before + 1
                assert (await post(host, f"{events_url}/corrections", public_body)).json() == {
                    **published.json(), "repeated": True}
                corrections = (await player.get(f"{events_url}/public-corrections")).json()
                assert len(corrections) == 1
                assert corrections[0]["source_event_id"] == start["id"]
                assert corrections[0]["text"] == public_body["corrected_text"]
                assert "Lý do chỉ quản trò" not in str(corrections)
                correction_revision = f"{base}/game/content/{published.json()['public_event_id']}/revisions"
                assert (await post(host, correction_revision, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Nội dung đính chính đã sửa"})).status_code == 200
                assert (await player.get(f"{events_url}/public-corrections")).json()[0]["text"] == "Nội dung đính chính đã sửa"
                assert (await post(host, correction_revision, {"client_request_id": str(uuid4()),
                    "action": "delete"})).status_code == 200
                assert (await player.get(f"{events_url}/public-corrections")).json() == []
                assert (await post(host, f"{events_url}/corrections", {
                    "client_request_id": str(uuid4()), "source_event_id": str(uuid4()),
                    "reason": "Sai", "corrected_text": "Đúng"})).status_code == 404
                async with session_factory()() as db:
                    original = await db.get(GameEvent, UUID(start["id"]))
                    assert original.kind == "game.started" and original.payload == start["payload"]
                    for event_id in (private.json()["event_id"], published.json()["event_id"],
                                     published.json()["public_event_id"]):
                        assert await db.scalar(select(Outbox).where(
                            Outbox.event_id == UUID(event_id))) is not None
            finally:
                if room_id:
                    async with session_factory()() as db:
                        room = await db.get(Room, UUID(room_id))
                        if room:
                            await db.delete(room)
                            await db.commit()
                async with session_factory()() as db:
                    for user_id in user_ids:
                        user = await db.get(User, UUID(user_id))
                        if user:
                            await db.delete(user)
                    await db.commit()

    asyncio.run(run())
