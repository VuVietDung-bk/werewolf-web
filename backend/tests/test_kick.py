"""Moderator kick removes room access without erasing historical game records."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select

from app.db.models import GameEvent, RoleAssignment, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_moderator_kick_across_room_phases() -> None:
    async def run() -> None:
        ids: list[str] = []
        transport = httpx.ASGITransport(app=app)
        async with AsyncExitStack() as stack:
            host, first, second, third, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(5)]
            try:
                for client, name in zip((host, first, second, third, outsider),
                                        ("Kick host", "Kick A", "Kick B", "Kick C", "Outsider")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Kick test", "seat_limit": 3})).json()
                base = f"/api/rooms/{room['id']}"
                for client in (first, second, third):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200

                c_kick = f"{base}/players/{ids[3]}/kick"
                assert (await post(first, c_kick, {"reason": "Không có quyền"})).status_code == 403
                assert (await post(outsider, c_kick, {"reason": "Không có quyền"})).status_code == 403
                assert (await post(host, f"{base}/players/{ids[0]}/kick", {
                    "reason": "Tự kick"})).status_code == 403
                assert (await post(host, c_kick, {"reason": "   "})).status_code == 422
                kicked_lobby = await post(host, c_kick, {"reason": "  Quấy rối sảnh  "})
                assert kicked_lobby.status_code == 200
                assert ids[3] not in [item["user_id"] for item in kicked_lobby.json()["members"]]
                assert (await third.get(base)).status_code == 403
                assert (await third.get("/api/rooms/mine")).json() == []
                assert (await post(third, "/api/rooms", {
                    "name": "Phòng khác", "seat_limit": 2})).status_code == 201
                other = (await third.get("/api/rooms/mine")).json()[0]
                assert (await post(third, f"/api/rooms/{other['id']}/cancel")).status_code == 204
                assert (await post(third, "/api/rooms/join", {
                    "code": room["code"], "accept_postgame_transcript": True})).status_code == 200

                role = {"name": "Dân làng", "description": "Role thử kick",
                        "faction_label": "Làng", "seer_result": "Thiện",
                        "role_kind": "passive", "quantity": 3}
                assert (await post(host, f"{base}/roles", role)).status_code == 201
                assert (await host.patch(f"{base}/settings", json={"name": "Kick test",
                    "seat_limit": 3, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, c_kick, {"reason": "Loại sau phát role"})).status_code == 200
                async with session_factory()() as db:
                    assert await db.get(RoleAssignment, (UUID(room["id"]), UUID(ids[3]))) is None
                assert (await post(host, f"{base}/game/start")).json()["status"] == "playing"

                vote = (await post(host, f"{base}/votes", {
                    "client_request_id": str(uuid4())})).json()
                b_kick = f"{base}/players/{ids[2]}/kick"
                assert (await post(host, b_kick, {"reason": "Đóng vote trước"})).status_code == 409
                assert (await post(host, f"{base}/votes/{vote['vote_round_id']}/close", {
                    "client_request_id": str(uuid4())})).status_code == 200
                request_id = str(uuid4())
                body = {"reason": "  Phá trận  ", "client_request_id": request_id}
                kicked_playing = await post(host, b_kick, body)
                assert kicked_playing.status_code == 200
                assert ids[2] not in [item["user_id"] for item in kicked_playing.json()["members"]]
                assert (await post(host, b_kick, body)).status_code == 200
                assert (await post(host, b_kick, {**body,
                    "reason": "Lý do khác"})).status_code == 409
                assert (await second.get(base)).status_code == 403
                assert (await second.get(f"{base}/chat/context")).status_code == 403
                assert (await second.get(f"{base}/roles/my-assignment")).status_code == 403
                assert (await post(second, f"{base}/votes/{vote['vote_round_id']}/ballots", {
                    "client_request_id": str(uuid4()), "choice": "skip"})).status_code == 403
                assert (await post(host, f"{base}/game/players/{ids[2]}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill",
                    "reason": "Không còn ở phòng"})).status_code == 409
                assert (await post(host, f"{base}/game/players/{ids[2]}/private-messages", {
                    "client_request_id": str(uuid4()), "text": "Không nhận được"})).status_code == 409
                assert (await post(second, "/api/rooms", {
                    "name": "Phòng sau kick", "seat_limit": 2})).status_code == 201

                assert (await post(host, f"{base}/game/finish", {
                    "result_label": "Kết thúc", "result_reason": "Kiểm tra kick"})).status_code == 200
                assert (await second.get(f"{base}/transcript")).status_code == 200
                assert (await third.get(f"{base}/transcript")).status_code == 200
                assert (await post(host, f"{base}/players/{ids[1]}/kick", {
                    "reason": "Không vào ván sau"})).status_code == 200
                assert (await first.get(f"{base}/transcript")).status_code == 200
                next_room = (await post(host, f"{base}/game/next")).json()
                assert next_room["members"] == []
                assert (await post(host, f"{base}/players/{ids[1]}/kick", {
                    "reason": "Đã mở ván mới"})).status_code == 409
                async with session_factory()() as db:
                    events = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room["id"]),
                        GameEvent.kind == "member.kicked").order_by(GameEvent.sequence))).all()
                    assert len(events) == 4
                    assert events[0].payload["request"]["reason"] == "Quấy rối sảnh"
                    assert events[2].payload["request"]["reason"] == "Phá trận"
            finally:
                async with session_factory()() as db:
                    owned = (await db.scalars(select(Room.id).where(
                        Room.owner_user_id.in_([UUID(item) for item in ids])))).all()
                    await db.execute(delete(Room).where(Room.id.in_(owned)))
                    await db.commit()
                    await db.execute(delete(User).where(User.id.in_([UUID(item) for item in ids])))
                    await db.commit()

    asyncio.run(run())


def test_kick_during_draft_advances_turn_and_keeps_remaining_choices() -> None:
    async def run() -> None:
        ids: list[str] = []
        transport = httpx.ASGITransport(app=app)
        async with AsyncExitStack() as stack:
            clients = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(5)]
            host, *players = clients
            try:
                for client, name in zip(clients, ("Draft kick host", "Draft kick A", "Draft kick B",
                                                   "Draft kick C", "Draft kick D")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Kick khi chọn role", "seat_limit": 4})).json()
                base = f"/api/rooms/{room['id']}"
                for player in players:
                    assert (await post(player, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                role_ids = []
                for number in range(4):
                    response = await post(host, f"{base}/roles", {
                        "name": f"Role {number + 1}", "description": "Vai thử draft",
                        "faction_label": "Làng", "seer_result": "Thiện",
                        "role_kind": "passive", "quantity": 1})
                    assert response.status_code == 201
                    role_ids.append(response.json()["id"])
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Kick khi chọn role", "seat_limit": 4, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                started = await post(host, f"{base}/roles/draft/start", {
                    "candidate_role_ids": role_ids})
                assert started.status_code == 200
                status = started.json()
                by_id = dict(zip(ids[1:], players))
                first_turn = next(turn for turn in status["host_turns"]
                                  if turn["turn_index"] == status["current_turn"])
                first_id = first_turn["user_id"]
                chosen = await post(by_id[first_id], f"{base}/roles/draft/choose", {
                    "choose_random": True})
                assert chosen.status_code == 200
                status = (await host.get(f"{base}/roles/draft/status")).json()
                second_turn = next(turn for turn in status["host_turns"]
                                   if turn["turn_index"] == status["current_turn"])
                second_id = second_turn["user_id"]

                assert (await post(host, f"{base}/players/{first_id}/kick", {
                    "reason": "Người đã chọn role rời phòng"})).status_code == 200
                async with session_factory()() as db:
                    assert await db.get(RoleAssignment, (UUID(room["id"]), UUID(first_id))) is None
                status = (await host.get(f"{base}/roles/draft/status")).json()
                assert status["total"] == 3 and status["current_turn"] == second_turn["turn_index"]
                assert (await post(host, f"{base}/players/{second_id}/kick", {
                    "reason": "Người đang đến lượt rời phòng"})).status_code == 200
                status = (await host.get(f"{base}/roles/draft/status")).json()
                assert status["total"] == 2 and status["current_turn"] is not None
                current = next(turn for turn in status["host_turns"]
                               if turn["turn_index"] == status["current_turn"])
                assert current["offers"]
                assert (await by_id[second_id].get(f"{base}/roles/draft/status")).status_code == 403
                while status["status"] == "drafting":
                    current = next(turn for turn in status["host_turns"]
                                   if turn["turn_index"] == status["current_turn"])
                    assert (await post(by_id[current["user_id"]], f"{base}/roles/draft/choose", {
                        "choose_random": True})).status_code == 200
                    status = (await host.get(f"{base}/roles/draft/status")).json()
                assert status["status"] == "assigned" and status["completed"] == 2
                assert (await post(host, f"{base}/game/start")).json()["status"] == "playing"
            finally:
                async with session_factory()() as db:
                    owned = (await db.scalars(select(Room.id).where(
                        Room.owner_user_id.in_([UUID(item) for item in ids])))).all()
                    await db.execute(delete(Room).where(Room.id.in_(owned)))
                    await db.commit()
                    await db.execute(delete(User).where(User.id.in_([UUID(item) for item in ids])))
                    await db.commit()

    asyncio.run(run())
