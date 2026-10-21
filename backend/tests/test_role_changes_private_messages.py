"""Moderator identity changes and private information stay scoped to one player."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.db.models import GameEvent, Outbox, PlayerState, PrivateMessage, RoleAssignment, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_role_changes_and_private_messages() -> None:
    async def run() -> None:
        user_ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, target, other, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(4)]
            try:
                for client, name in zip((host, target, other, outsider),
                                        ("Quản trò đổi role", "Người nhận", "Người khác", "Ngoài phòng")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Đổi role và tin riêng", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                for client in (target, other):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                for name, faction, team in (("Dân", "Làng", None), ("Sói", "Sói", "Bầy Sói")):
                    assert (await post(host, f"{base}/roles", {
                        "name": name, "description": f"Mô tả {name}", "faction_label": faction,
                        "seer_result": faction, "team_chat_label": team,
                        "role_kind": "passive", "quantity": 1})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Đổi role và tin riêng", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                original = (await target.get(f"{base}/roles/my-assignment")).json()["role"]
                catalog = (await target.get(f"{base}/role-catalog")).json()
                replacement = next(role for role in catalog["roles"] if role["id"] != original["id"])
                assert (await post(host, f"{base}/game/start")).status_code == 200
                public_version = (await other.get(base)).json()["version"]
                actions = f"{base}/game/players/{user_ids[1]}/actions"
                role_body = {"client_request_id": str(uuid4()), "action": "change_role",
                             "role_id": replacement["id"], "reason": "Chuyển hóa trong ván"}
                assert (await post(target, actions, role_body)).status_code == 403
                assert (await post(host, actions, {**role_body, "role_id": str(uuid4()),
                                                  "client_request_id": str(uuid4())})).status_code == 422
                changed = await post(host, actions, role_body)
                assert changed.status_code == 200 and not changed.json()["repeated"]
                assert (await post(host, actions, role_body)).json()["repeated"]
                assert (await post(host, actions, {**role_body, "reason": "Khác"})).status_code == 409
                current = (await target.get(f"{base}/roles/my-assignment")).json()["role"]
                assert current["id"] == replacement["id"]
                assert current["faction_label"] == replacement["faction_label"]
                assert current["team_chat_label"] == replacement["team_chat_label"]
                assert (await other.get(f"{base}/roles/my-assignment")).json()["role"]["id"] == replacement["id"]
                assert (await target.get(f"{base}/role-catalog")).json() == catalog
                assert (await other.get(base)).json()["version"] == public_version
                assert (await outsider.get(f"{base}/roles/my-assignment")).status_code == 403

                faction_body = {"client_request_id": str(uuid4()), "action": "change_faction",
                                "text": "Trung lập", "reason": "Đã xác nhận phản bội"}
                assert (await post(host, actions, faction_body)).status_code == 200
                team_body = {"client_request_id": str(uuid4()), "action": "change_team",
                             "text": "Hội kín", "reason": "Gia nhập hội"}
                assert (await post(host, actions, team_body)).status_code == 200
                current = (await target.get(f"{base}/roles/my-assignment")).json()["role"]
                assert (current["faction_label"], current["team_chat_label"]) == ("Trung lập", "Hội kín")
                assert (await post(host, actions, {**team_body, "client_request_id": str(uuid4()),
                                                  "text": None})).status_code == 200
                assert (await target.get(f"{base}/roles/my-assignment")).json()["role"]["team_chat_label"] is None
                board = (await host.get(f"{base}/game/players")).json()
                assert next(item for item in board if item["user_id"] == user_ids[1])["role"]["faction_label"] == "Trung lập"

                message_url = f"{base}/game/players/{user_ids[1]}/private-messages"
                inbox = f"{base}/game/private-messages"
                message_body = {"client_request_id": str(uuid4()), "text": "Bạn đã nhận lời tiên tri.",
                                "reason": "Xác nhận thông tin sau đổi phe"}
                assert (await post(target, message_url, message_body)).status_code == 403
                sent = await post(host, message_url, message_body)
                assert sent.status_code == 200 and not sent.json()["repeated"]
                assert (await post(host, message_url, message_body)).json()["repeated"]
                assert (await post(host, message_url, {**message_body, "text": "Khác"})).status_code == 409
                assert (await target.get(inbox)).json()[0]["text"] == message_body["text"]
                assert "reason" not in (await target.get(inbox)).json()[0]
                assert (await host.get(message_url)).json() == (await target.get(inbox)).json()
                assert (await other.get(inbox)).json() == []
                assert (await other.get(message_url)).status_code == 403
                assert (await outsider.get(inbox)).status_code == 403
                assert (await other.get(base)).json()["version"] == public_version

                async with session_factory()() as db:
                    assignment = await db.get(RoleAssignment, (UUID(room_id), UUID(user_ids[1])))
                    state = await db.get(PlayerState, (UUID(room_id), UUID(user_ids[1])))
                    assert str(assignment.role_id) == original["id"]
                    assert str(state.current_role_id) == replacement["id"]
                    assert state.faction_label_override == "Trung lập"
                    assert state.team_chat_override_set and state.team_chat_label_override is None
                    messages = (await db.scalars(select(PrivateMessage).where(
                        PrivateMessage.room_id == UUID(room_id)))).all()
                    assert len(messages) == 1 and messages[0].text == message_body["text"]
                    events = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind.like("player.%")
                    ).order_by(GameEvent.sequence))).all()
                    assert [event.kind for event in events] == [
                        "player.change_role", "player.change_faction", "player.change_team",
                        "player.change_team", "player.private_message_sent"]
                    assert all(event.visibility == "host" for event in events)
                    for event in events:
                        assert (await db.scalar(select(Outbox).where(Outbox.event_id == event.id))) is not None

                assert (await post(host, actions, {"client_request_id": str(uuid4()),
                    "action": "kill", "reason": "Kiểm tra công bố role hiện tại"})).status_code == 200
                assert (await post(host, f"{base}/game/players/{user_ids[1]}/announce", {
                    "client_request_id": str(uuid4()), "moderator_reason": "Công bố cái chết",
                    "reveal_role": True})).status_code == 200
                notices = (await other.get(f"{base}/game/death-notices")).json()
                assert notices[-1]["revealed_role_name"] == replacement["name"]

                assert (await post(host, f"{base}/game/pause", {"reason": "Nghỉ"})).status_code == 200
                assert (await post(host, actions, {**faction_body,
                    "client_request_id": str(uuid4()), "text": "Làng"})).status_code == 409
                assert (await post(host, message_url, {**message_body,
                    "client_request_id": str(uuid4()), "text": "Vẫn nhận được"})).status_code == 200
                assert len((await target.get(inbox)).json()) == 2
                assert (await post(host, f"{base}/game/finish", {
                    "result_label": "Kết thúc", "result_reason": "Đã kiểm tra"})).status_code == 200
                assert (await post(host, message_url, {**message_body,
                    "client_request_id": str(uuid4())})).status_code == 409
                assert (await post(host, message_url, message_body)).json() == {
                    "event_id": sent.json()["event_id"], "repeated": True}
                assert len((await target.get(inbox)).json()) == 2
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
