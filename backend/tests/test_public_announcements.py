"""Custom public notices remain host-controlled and keep an audit trail."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

import app.event_corrections as event_corrections
from app.db.models import ContentRevision, GameEvent, Outbox, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_custom_announcement_publish_edit_delete_and_permissions(monkeypatch) -> None:
    async def run() -> None:
        user_ids: list[str] = []
        room_id: str | None = None
        original_append = event_corrections.append_event
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            host, player, second, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(4)]
            try:
                for client, name in zip((host, player, second, outsider),
                                        ("Quản trò thông báo", "Người trong phòng", "Người thứ hai", "Người ngoài")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Thử thông báo", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                announcements_url = f"{base}/game/events/announcements"
                for client in (player, second):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await outsider.get(announcements_url)).status_code == 403
                assert (await post(player, announcements_url, {
                    "client_request_id": str(uuid4()), "text": "Sai quyền"})).status_code == 403
                assert (await post(host, announcements_url, {
                    "client_request_id": str(uuid4()), "text": "Chưa bắt đầu"})).status_code == 409
                assert (await post(host, f"{base}/roles", {
                    "name": "Dân", "description": "Kiểm tra", "faction_label": "Làng",
                    "seer_result": "Thiện", "role_kind": "passive", "quantity": 2})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Thử thông báo", "seat_limit": 2, "registration_open": False,
                }, headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200
                assert (await player.get(announcements_url)).json() == []
                assert (await post(host, announcements_url, {
                    "client_request_id": str(uuid4()), "text": "   "})).status_code == 422

                original_append = event_corrections.append_event

                async def fail_append(*args, **kwargs):
                    raise RuntimeError("test event failure")

                version_before = (await player.get(base)).json()["version"]
                monkeypatch.setattr(event_corrections, "append_event", fail_append)
                failed = await post(host, announcements_url, {
                    "client_request_id": str(uuid4()), "text": "Không được công bố"})
                assert failed.status_code == 500
                monkeypatch.setattr(event_corrections, "append_event", original_append)
                assert (await player.get(announcements_url)).json() == []
                assert (await player.get(base)).json()["version"] == version_before

                body = {"client_request_id": str(uuid4()), "text": "  Dân làng tập hợp.  "}
                published = await post(host, announcements_url, body)
                assert published.status_code == 200 and published.json()["repeated"] is False
                notice_id = published.json()["event_id"]
                assert (await post(host, announcements_url, body)).json() == {
                    "event_id": notice_id, "repeated": True}
                assert (await post(host, announcements_url, {**body,
                    "text": "Nội dung khác"})).status_code == 409
                visible = (await player.get(announcements_url)).json()
                assert len(visible) == 1 and visible[0]["text"] == "Dân làng tập hợp."
                assert visible[0]["phase_type"] == "day" and visible[0]["round_number"] == 0
                assert (await player.get(base)).json()["version"] == version_before + 1

                revision_url = f"{base}/game/content/{notice_id}/revisions"
                assert (await post(player, revision_url, {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "Sai quyền"})).status_code == 403
                assert (await post(host, revision_url, {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "  "})).status_code == 422
                edited = await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Dân làng tập hợp ở quảng trường."})
                assert edited.status_code == 200 and edited.json()["public_event_id"]
                assert (await player.get(announcements_url)).json()[0]["text"] == "Dân làng tập hợp ở quảng trường."
                assert (await player.get(announcements_url)).json()[0]["edited_at"]
                deleted = await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "delete"})
                assert deleted.status_code == 200 and deleted.json()["public_event_id"]
                assert (await player.get(announcements_url)).json() == []
                assert (await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Khôi phục"})).status_code == 409

                async with session_factory()() as db:
                    source = await db.get(GameEvent, UUID(notice_id))
                    assert source.kind == "event.custom_announcement"
                    assert source.payload["text"] == "Dân làng tập hợp."
                    assert (await db.get(ContentRevision, UUID(notice_id))).deleted_at
                    assert await db.scalar(select(Outbox).where(Outbox.event_id == source.id))
                    for event_id in (edited.json()["event_id"], edited.json()["public_event_id"],
                                     deleted.json()["event_id"], deleted.json()["public_event_id"]):
                        assert await db.scalar(select(Outbox).where(
                            Outbox.event_id == UUID(event_id))) is not None
            finally:
                monkeypatch.setattr(event_corrections, "append_event", original_append)
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
