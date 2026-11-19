"""Routine moderator actions can omit reasons while consequential choices still need one."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.db.models import GameEvent, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_optional_moderator_reasons() -> None:
    async def run() -> None:
        user_ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, first, second = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(3)]
            try:
                for client, name in zip((host, first, second), ("Quản trò lý do", "Người thứ nhất", "Người thứ hai")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Thử lý do tùy chọn", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                for client in (first, second):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                role = await post(host, f"{base}/roles", {
                    "name": "Dân thử", "description": "Mô tả", "faction_label": "Làng",
                    "seer_result": "Thiện", "role_kind": "passive", "quantity": 2})
                assert role.status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Thử lý do tùy chọn", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200

                action_url = f"{base}/game/players/{user_ids[1]}/actions"
                for action, extra in (
                    ("add_effect", {"text": "Được bảo vệ"}),
                    ("remove_effect", {"text": "Được bảo vệ"}),
                    ("change_faction", {"text": "Trung lập"}),
                    ("change_team", {"text": "Hội kín"}),
                    ("change_role", {"role_id": role.json()["id"]}),
                    ("kill", {}),
                ):
                    response = await post(host, action_url, {
                        "client_request_id": str(uuid4()), "action": action, **extra})
                    assert response.status_code == 200, (action, response.text)
                announced = await post(host, f"{base}/game/players/{user_ids[1]}/announce", {
                    "client_request_id": str(uuid4()), "public_cause": "Đã chết"})
                assert announced.status_code == 200, announced.text
                revived = await post(host, action_url, {
                    "client_request_id": str(uuid4()), "action": "revive", "reason": "  "})
                assert revived.status_code == 200, revived.text
                policy = {"client_request_id": str(uuid4()), "common_send_at_night": True,
                          "team_send_in_day": False, "dead_common_send": False,
                          "dead_team_send": False}
                changed = await host.patch(f"{base}/chat/policy", json=policy, headers={"Origin": ORIGIN})
                assert changed.status_code == 200, changed.text
                assert (await post(host, f"{base}/game/pause", {})).status_code == 422
                assert (await post(host, f"{base}/game/players/{user_ids[1]}/publication-disposition", {
                    "client_request_id": str(uuid4()), "decision": "skip"})).status_code == 422

                async with session_factory()() as db:
                    events = (await db.scalars(select(GameEvent).where(GameEvent.room_id == UUID(room_id)))).all()
                    routine_kinds = {"player.add_effect", "player.remove_effect", "player.change_faction",
                                     "player.change_team", "player.change_role", "player.kill", "player.revive"}
                    routine = [event for event in events if event.kind in routine_kinds]
                    assert len(routine) == len(routine_kinds)
                    assert all(event.payload["request"]["reason"] is None for event in routine)
                    assert next(event for event in events if event.kind == "player.announcement_decided").payload["request"]["moderator_reason"] is None
                    assert next(event for event in events if event.kind == "chat.policy_changed").payload["request"]["reason"] is None
            finally:
                if room_id:
                    async with session_factory()() as db:
                        room_uuid = UUID(room_id)
                        await db.delete(await db.get(Room, room_uuid))
                        await db.commit()
                        for user_id in user_ids:
                            user = await db.get(User, UUID(user_id))
                            if user:
                                await db.delete(user)
                        await db.commit()

    asyncio.run(run())
