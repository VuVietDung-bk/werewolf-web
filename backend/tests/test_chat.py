"""Chat persistence, pagination, and channel permissions across phase and team changes."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.auth import COOKIE_NAME
from app.db.models import GameEvent, Outbox, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def message(channel, text, **extra):
    return {"client_request_id": str(uuid4()), "channel": channel, "text": text, **extra}


def test_chat_permissions_and_history() -> None:
    async def run() -> None:
        ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            clients = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(5)]
            host, first, second, third, outsider = clients
            try:
                for client, name in zip(clients, ("Quản trò chat", "A", "B", "C", "Ngoài phòng")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Thử chat", "seat_limit": 3})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                chat = f"{base}/chat"
                for client in (first, second, third):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await outsider.get(f"{chat}/messages?channel=common")).status_code == 403
                assert (await post(outsider, f"{chat}/messages", message("common", "Lén vào"))).status_code == 403

                lobby = message("common", "Tin ở sảnh")
                first_sent = await post(first, f"{chat}/messages", lobby)
                assert first_sent.status_code == 200 and not first_sent.json()["repeated"]
                assert (await post(first, f"{chat}/messages", lobby)).json()["repeated"]
                assert (await post(first, f"{chat}/messages", {**lobby, "text": "Khác"})).status_code == 409
                for index in range(32):
                    assert (await post(first, f"{chat}/messages", message("common", f"Sảnh {index}"))).status_code == 200
                newest = (await second.get(f"{chat}/messages?channel=common")).json()
                assert len(newest["messages"]) == 30 and newest["next_before_sequence"]
                older = (await second.get(f"{chat}/messages?channel=common&before_sequence={newest['next_before_sequence']}")).json()
                assert len(older["messages"]) == 3 and older["messages"][0]["text"] == "Tin ở sảnh"
                assert older["next_before_sequence"] is None
                forward = (await second.get(f"{chat}/messages?channel=common&after_sequence=0&limit=10")).json()
                assert len(forward["messages"]) == 10 and forward["next_after_sequence"]
                assert forward["messages"][0]["text"] == "Tin ở sảnh"
                assert (await second.get(f"{chat}/messages?channel=common&before_sequence=10&after_sequence=0")).status_code == 422
                assert (await second.get(f"{chat}/messages?channel=common&limit=51")).status_code == 422

                for name, count, team, kind, contact in (
                    ("Sói thử", 2, "Bầy sói", "active", {"can_initiate": True, "initiate_phases": ["night"], "reply_allowed": True}),
                    ("Dân thử", 1, None, "passive", {"reply_allowed": True}),
                    ("Dân không đáp", 0, None, "passive", {"reply_allowed": False}),
                ):
                    assert (await post(host, f"{base}/roles", {
                        "name": name, "description": name, "faction_label": "Sói" if team else "Làng",
                        "seer_result": "Ác" if team else "Thiện", "team_chat_label": team,
                        "role_kind": kind, "quantity": count, "host_contact": contact})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Thử chat", "seat_limit": 3, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(second, f"{chat}/messages", message("common", "Sảnh đã đóng đăng ký"))).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assigned = (await host.get(f"{base}/roles/assignments")).json()
                by_id = dict(zip(ids[1:4], clients[1:4]))
                wolves = [item["user_id"] for item in assigned if item["role"]["team_chat_label"]]
                villager = next(item["user_id"] for item in assigned if not item["role"]["team_chat_label"])
                wolf_a, wolf_b = wolves
                assert (await post(host, f"{base}/game/start")).status_code == 200
                assert (await post(by_id[villager], f"{chat}/messages", message("common", "Ngày 0"))).status_code == 200
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("team", "Quá sớm"))).status_code == 403
                assert (await post(by_id[villager], f"{chat}/messages", message("host", "Tôi muốn hỏi"))).status_code == 403
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("host", "Chưa đến đêm"))).status_code == 403
                assert (await post(host, f"{chat}/messages", message("host", "Quản trò mở chat", participant_user_id=villager))).status_code == 200
                assert (await post(by_id[villager], f"{chat}/messages", message("host", "Tôi trả lời"))).status_code == 200
                assert (await by_id[wolf_a].get(f"{chat}/messages?channel=host&participant_user_id={villager}")).status_code == 403
                assert (await by_id[wolf_a].get(f"{chat}/messages?channel=host")).json()["messages"] == []
                assert [item["text"] for item in (await by_id[villager].get(f"{chat}/messages?channel=host")).json()["messages"]] == [
                    "Quản trò mở chat", "Tôi trả lời"]
                silent_role_id = next(item["id"] for item in (await host.get(f"{base}/role-catalog")).json()["roles"]
                                      if item["name"] == "Dân không đáp")
                assert (await post(host, f"{base}/game/players/{villager}/actions", {
                    "client_request_id": str(uuid4()), "action": "change_role", "role_id": silent_role_id,
                    "reason": "Kiểm tra role không cho trả lời"})).status_code == 200
                assert (await post(by_id[villager], f"{chat}/messages", message("host", "Không thể đáp"))).status_code == 403

                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                assert (await post(first, f"{chat}/messages", message("common", "Tin đêm"))).status_code == 403
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("team", "Tin bầy cũ"))).status_code == 200
                assert [item["text"] for item in (await by_id[wolf_b].get(f"{chat}/messages?channel=team")).json()["messages"]] == ["Tin bầy cũ"]
                assert (await by_id[villager].get(f"{chat}/messages?channel=team")).json()["messages"] == []
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("host", "Hỏi đêm"))).status_code == 200

                actions = f"{base}/game/players"
                assert (await post(host, f"{actions}/{wolf_b}/actions", {
                    "client_request_id": str(uuid4()), "action": "change_team", "text": "Đội mới", "reason": "Đổi phe"})).status_code == 200
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("team", "Sau khi B rời"))).status_code == 200
                assert [item["text"] for item in (await by_id[wolf_b].get(f"{chat}/messages?channel=team")).json()["messages"]] == ["Tin bầy cũ"]
                assert (await post(host, f"{actions}/{villager}/actions", {
                    "client_request_id": str(uuid4()), "action": "change_team", "text": "Bầy sói", "reason": "Gia nhập"})).status_code == 200
                assert [item["text"] for item in (await by_id[villager].get(f"{chat}/messages?channel=team")).json()["messages"]] == []
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("team", "Sau khi C vào"))).status_code == 200
                assert [item["text"] for item in (await by_id[villager].get(f"{chat}/messages?channel=team")).json()["messages"]] == ["Sau khi C vào"]
                assert (await post(host, f"{actions}/{wolf_a}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill", "reason": "Kiểm tra quyền"})).status_code == 200
                assert (await by_id[wolf_a].get(f"{chat}/context")).json()["can_send_team"] is False
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("team", "Chết vẫn nhắn"))).status_code == 403
                assert (await post(host, f"{chat}/messages", message("team", "Host gửi đội", team_label="Bầy sói"))).status_code == 200
                assert "Host gửi đội" not in [item["text"] for item in (await by_id[wolf_a].get(f"{chat}/messages?channel=team")).json()["messages"]]
                assert "Host gửi đội" in [item["text"] for item in (await by_id[villager].get(f"{chat}/messages?channel=team")).json()["messages"]]

                assert (await post(host, f"{base}/game/pause", {"reason": "Nghỉ"})).status_code == 200
                assert (await post(first, f"{chat}/messages", message("common", "Nói lúc pause"))).status_code == 409
                assert (await post(by_id[villager], f"{chat}/messages", message("host", "Nói lúc pause"))).status_code == 409
                assert (await post(host, f"{chat}/messages", message("team", "Nói lúc pause", team_label="Bầy sói"))).status_code == 409

                assert (await post(host, f"{base}/game/resume")).status_code == 200
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "night", "expected_round_number": 1})).status_code == 200
                assert (await by_id[wolf_a].get(f"{chat}/context")).json()["can_send_common"] is False
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("common", "Chết nói ngày"))).status_code == 403
                policy = {"client_request_id": str(uuid4()), "common_send_at_night": True,
                          "team_send_in_day": True, "dead_common_send": True,
                          "dead_team_send": True, "reason": "Luật đặc biệt của ván"}
                assert (await first.patch(f"{chat}/policy", json=policy, headers={"Origin": ORIGIN})).status_code == 403
                changed = await host.patch(f"{chat}/policy", json=policy, headers={"Origin": ORIGIN})
                assert changed.status_code == 200 and changed.json()["repeated"] is False
                assert (await host.patch(f"{chat}/policy", json=policy, headers={"Origin": ORIGIN})).json()["repeated"]
                assert (await host.patch(f"{chat}/policy", json={**policy, "reason": "Khác"},
                                         headers={"Origin": ORIGIN})).status_code == 409
                assert (await by_id[wolf_a].get(f"{chat}/context")).json()["can_send_common"] is True
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("common", "Chết nói theo luật"))).status_code == 200
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("team", "Chết nói đội ban ngày"))).status_code == 200
                assert "Chết nói đội ban ngày" in [item["text"] for item in (
                    await by_id[villager].get(f"{chat}/messages?channel=team")).json()["messages"]]
                default_policy = {**policy, "client_request_id": str(uuid4()),
                                  "common_send_at_night": False, "team_send_in_day": False,
                                  "dead_common_send": False, "dead_team_send": False,
                                  "reason": "Trở lại luật mặc định"}
                assert (await host.patch(f"{chat}/policy", json=default_policy,
                                         headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(by_id[wolf_a], f"{chat}/messages", message("common", "Lại bị chặn"))).status_code == 403
                assert (await by_id[wolf_a].get(f"{chat}/messages?channel=team")).json()["messages"][-1]["text"] == "Chết nói đội ban ngày"
                async with session_factory()() as db:
                    changes = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind == "chat.policy_changed"
                    ).order_by(GameEvent.sequence))).all()
                    assert len(changes) == 2
                    assert changes[0].payload["previous"]["dead_team_send"] is False
                    assert changes[0].payload["current"]["dead_team_send"] is True
                    assert changes[1].payload["current"]["dead_team_send"] is False
                    for change in changes:
                        assert change.visibility == "room"
                        assert (await db.scalar(select(Outbox.id).where(
                            Outbox.event_id == change.id))) is not None

                async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                             cookies={COOKIE_NAME: first.cookies.get(COOKIE_NAME)}) as restored:
                    assert (await restored.get(f"{chat}/messages?channel=common&limit=50")).json()["messages"][0]["text"] == "Tin ở sảnh"
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())


def test_common_chat_during_role_draft() -> None:
    async def run() -> None:
        ids = []
        room_id = None
        transport = httpx.ASGITransport(app=app)
        async with (httpx.AsyncClient(transport=transport, base_url="http://test") as host,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as first,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as second):
            try:
                for client, name in ((host, "Draft host"), (first, "Draft A"), (second, "Draft B")):
                    result = await post(client, "/api/auth/guest", {"display_name": name})
                    assert result.status_code == 200
                    ids.append(result.json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Chat khi chọn role", "seat_limit": 2})).json()
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                for client in (first, second):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                role_ids = []
                for index in range(2):
                    role = await post(host, f"{base}/roles", {
                        "name": f"Role {index}", "description": "Mô tả", "faction_label": "Làng",
                        "seer_result": "Thiện", "role_kind": "passive"})
                    assert role.status_code == 201
                    role_ids.append(role.json()["id"])
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Chat khi chọn role", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                started = await post(host, f"{base}/roles/draft/start", {"candidate_role_ids": role_ids})
                assert started.status_code == 200 and started.json()["status"] == "drafting"
                chat = f"{base}/chat/messages"
                assert (await post(first, chat, message("common", "Đang chọn role"))).status_code == 200
                assert [item["text"] for item in (await second.get(f"{chat}?channel=common")).json()["messages"]] == ["Đang chọn role"]
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())
