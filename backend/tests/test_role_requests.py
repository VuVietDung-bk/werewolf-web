"""Free-text role requests obey live role, phase, privacy, and rate rules."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app import role_requests
from app.db.models import GameEvent, Outbox, PlayerState, RoleRequest, Room, User
from app.db.session import session_factory
from app.live import may_receive
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def request(text: str):
    return {"client_request_id": str(uuid4()), "text": text}


def test_role_requests_free_text_permissions_and_privacy(monkeypatch) -> None:
    async def run() -> None:
        room_id = None
        ids = []
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            clients = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(5)]
            host, first, second, third, outsider = clients
            try:
                for client, name in zip(clients, ("Request host", "A", "B", "C", "Outsider")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Yêu cầu role", "seat_limit": 3})).json()
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                requests = f"{base}/role-requests"
                for client in (first, second, third):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                roles = {}
                for name, kind, phases, quantity, when_dead in (
                    ("Dùng ngày", "active", ["day"], 1, False),
                    ("Dùng đêm", "active", ["night"], 1, False),
                    ("Bị động", "passive", [], 1, False),
                    ("Dùng đêm khi chết", "active", ["night"], 0, True),
                ):
                    created = await post(host, f"{base}/roles", {
                        "name": name, "description": f"Điều kiện của {name}", "faction_label": "Làng",
                        "seer_result": "Thiện", "role_kind": kind, "request_phases": phases,
                        "request_when_dead": when_dead, "quantity": quantity})
                    assert created.status_code == 201
                    roles[name] = created.json()["id"]
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Yêu cầu role", "seat_limit": 3, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assignments = (await host.get(f"{base}/roles/assignments")).json()
                by_id = dict(zip(ids[1:4], clients[1:4]))
                by_role = {item["role"]["name"]: (item["user_id"], by_id[item["user_id"]])
                           for item in assignments}
                day_id, day = by_role["Dùng ngày"]
                night_id, night = by_role["Dùng đêm"]
                passive_id, passive = by_role["Bị động"]

                assert (await post(day, requests, request("Trước trận"))).status_code == 403
                assert (await outsider.get(requests)).status_code == 403
                assert (await outsider.get(f"{requests}/context")).status_code == 403
                assert (await post(outsider, requests, request("Lén gửi"))).status_code == 403
                assert (await post(host, requests, request("Host gửi"))).status_code == 403
                assert (await post(host, f"{base}/game/start")).status_code == 200
                assert (await day.get(f"{requests}/context")).json()["can_submit"] is True
                assert (await night.get(f"{requests}/context")).json()["can_submit"] is False
                assert (await passive.get(f"{requests}/context")).json()["can_submit"] is False
                assert (await post(night, requests, request("Sai pha"))).status_code == 403
                assert (await post(passive, requests, request("Bị động tự gửi"))).status_code == 403
                assert (await post(day, requests, request("   "))).status_code == 422
                assert (await post(day, requests, request("x" * 2001))).status_code == 422
                assert (await post(day, requests, {**request("Đúng"), "target_id": day_id})).status_code == 422

                texts = ["3", "A, B và C", "Không có mục tiêu"]
                sent = []
                for text in texts:
                    body = request(text)
                    response = await post(day, requests, body)
                    assert response.status_code == 200 and response.json()["repeated"] is False
                    sent.append((body, response.json()))
                repeated = await post(day, requests, sent[0][0])
                assert repeated.status_code == 200 and repeated.json()["id"] == sent[0][1]["id"]
                assert repeated.json()["repeated"] is True
                assert (await post(day, requests, {**sent[0][0], "text": "Thay đổi"})).status_code == 409
                burst = await post(day, requests, request("Gửi quá nhanh"))
                assert burst.status_code == 429 and burst.headers["Retry-After"] == "30"
                monkeypatch.setattr(role_requests, "REQUEST_BURST_LIMIT", 100)
                monkeypatch.setattr(role_requests, "REQUEST_WINDOW_LIMIT", 3)
                window = await post(day, requests, request("Vượt 5 phút"))
                assert window.status_code == 429 and window.headers["Retry-After"] == "300"
                monkeypatch.setattr(role_requests, "REQUEST_WINDOW_LIMIT", 100)

                own = (await day.get(requests)).json()
                assert [item["text"] for item in own["requests"]] == texts
                assert all(item["role_name"] == "Dùng ngày" and item["phase_type"] == "day"
                           and item["round_number"] == 0 for item in own["requests"])
                assert (await night.get(requests)).json()["requests"] == []
                assert (await passive.get(requests)).json()["requests"] == []
                host_list = (await host.get(requests)).json()
                assert [item["text"] for item in host_list["requests"]] == texts
                assert (await day.get(f"{base}/game/players")).status_code == 403
                board = (await host.get(f"{base}/game/players")).json()
                assert {item["user_id"]: item["pending_request_count"] for item in board} == {
                    day_id: 3, night_id: 0, passive_id: 0}
                after = (await host.get(f"{requests}?after_sequence={host_list['requests'][0]['sequence']}&limit=1")).json()
                assert len(after["requests"]) == 1 and after["next_after_sequence"]
                assert (await host.get(f"{requests}?before_sequence=2&after_sequence=0")).status_code == 422
                async with session_factory()() as db:
                    rows = (await db.scalars(select(RoleRequest).where(RoleRequest.room_id == UUID(room_id)))).all()
                    assert len(rows) == 3 and all(row.status == "pending" for row in rows)
                    event = await db.get(GameEvent, UUID(sent[0][1]["event_id"]))
                    assert event.visibility == "host" and event.payload["request"]["text"] == "3"
                    assert may_receive(event, UUID(day_id), UUID(ids[0]), None, None)
                    assert not may_receive(event, UUID(night_id), UUID(ids[0]), None, None)
                    assert not may_receive(event, UUID(passive_id), UUID(ids[0]), None, None)
                    assert (await db.scalar(select(Outbox.id).where(Outbox.event_id == event.id))) is not None
                    state = await db.get(PlayerState, (UUID(room_id), UUID(day_id)))
                    assert state.is_alive and state.effects == []

                # A review of an old row must appear in the update cursor, with no leak to others.
                first_id, second_id, third_id = [item[1]["id"] for item in sent]
                cursor = host_list["room_cursor"]
                review_path = f"{requests}/{first_id}/review"
                progress = {"client_request_id": str(uuid4()), "status": "in_review",
                            "reply_text": "Đang kiểm tra", "linked_event_ids": []}
                assert (await post(day, review_path, progress)).status_code == 403
                assert (await post(host, review_path, progress)).status_code == 200
                assert (await post(host, review_path, progress)).json()["repeated"] is True
                changed = (await host.get(f"{requests}?after_updated_sequence={cursor}")).json()
                assert [item["id"] for item in changed["requests"]] == [first_id]
                assert changed["requests"][0]["status"] == "in_review"
                assert next(item for item in (await host.get(f"{base}/game/players")).json()
                            if item["user_id"] == day_id)["pending_request_count"] == 3
                assert (await night.get(f"{requests}?after_updated_sequence={cursor}")).json()["requests"] == []
                own = (await day.get(requests)).json()["requests"]
                assert own[0]["reply_text"] == "Đang kiểm tra" and own[0]["linked_event_ids"] == []
                assert (await post(host, review_path, {**progress, "reply_text": "Thay đổi"})).status_code == 409
                assert (await host.get(f"{requests}?after_updated_sequence=1&after_sequence=1")).status_code == 422

                assert (await post(host, f"{base}/game/players/{night_id}/actions", {
                    "client_request_id": str(uuid4()), "action": "add_effect", "text": "Đã soi"
                })).status_code == 422
                action = await post(host, f"{base}/game/players/{night_id}/actions", {
                    "client_request_id": str(uuid4()), "action": "add_effect",
                    "text": "Đã soi", "reason": "Theo yêu cầu"})
                assert action.status_code == 200
                action_id = action.json()["event_id"]
                assert (await night.get(f"{requests}/{first_id}/linkable-actions")).status_code == 403
                linkable = (await host.get(f"{requests}/{first_id}/linkable-actions")).json()
                assert any(item["event_id"] == action_id for item in linkable)
                assert (await post(host, review_path, {"client_request_id": str(uuid4()),
                    "status": "accepted", "reply_text": "Đã soi",
                    "linked_event_ids": [sent[0][1]["event_id"]]})).status_code == 422
                final = {"client_request_id": str(uuid4()), "status": "accepted",
                         "reply_text": "Đã soi", "linked_event_ids": [action_id]}
                assert (await post(host, review_path, final)).status_code == 200
                assert (await post(host, review_path, final)).json()["repeated"] is True
                assert (await post(host, review_path, {**final, "client_request_id": str(uuid4())})).status_code == 409
                assert (await host.get(requests)).json()["requests"][0]["linked_event_ids"] == [action_id]
                own = (await day.get(requests)).json()["requests"][0]
                assert own["status"] == "accepted" and own["reply_text"] == "Đã soi"
                assert own["linked_event_ids"] == []
                assert next(item for item in (await host.get(f"{base}/game/players")).json()
                            if item["user_id"] == day_id)["pending_request_count"] == 2

                reject_path = f"{requests}/{second_id}/review"
                assert (await post(host, reject_path, {"client_request_id": str(uuid4()),
                    "status": "rejected", "reply_text": " "})).status_code == 422
                assert (await post(host, reject_path, {"client_request_id": str(uuid4()),
                    "status": "rejected", "reply_text": "Không hợp lệ"})).status_code == 200
                assert (await post(night, f"{requests}/{third_id}/withdraw", {
                    "client_request_id": str(uuid4())})).status_code == 404
                withdrawal = {"client_request_id": str(uuid4())}
                assert (await post(day, f"{requests}/{third_id}/withdraw", withdrawal)).status_code == 200
                assert (await post(day, f"{requests}/{third_id}/withdraw", withdrawal)).json()["repeated"] is True
                assert (await post(host, f"{requests}/{third_id}/review", {"client_request_id": str(uuid4()),
                    "status": "accepted", "reply_text": "Đã làm"})).status_code == 409
                assert [item["status"] for item in (await day.get(requests)).json()["requests"]] == [
                    "accepted", "rejected", "withdrawn"]
                assert all(item["pending_request_count"] == 0
                           for item in (await host.get(f"{base}/game/players")).json())
                async with session_factory()() as db:
                    reviewed = await db.scalar(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind == "role.request_reviewed",
                        GameEvent.payload["request"]["request_id"].astext == first_id,
                        GameEvent.payload["request"]["status"].astext == "accepted"))
                    assert reviewed is not None and reviewed.payload["request"]["linked_event_ids"] == [action_id]
                    assert may_receive(reviewed, UUID(day_id), UUID(ids[0]), None, None)
                    assert not may_receive(reviewed, UUID(night_id), UUID(ids[0]), None, None)
                    assert (await db.scalar(select(Outbox.id).where(Outbox.event_id == reviewed.id))) is not None

                assert (await post(host, f"{base}/game/pause", {"reason": "Nghỉ"})).status_code == 200
                assert (await post(day, requests, request("Trong lúc nghỉ"))).status_code == 403
                assert (await post(host, f"{base}/game/resume")).status_code == 200
                actions = f"{base}/game/players"
                assert (await post(host, f"{actions}/{day_id}/actions", {
                    "client_request_id": str(uuid4()), "action": "change_role",
                    "role_id": roles["Bị động"], "reason": "Kiểm tra role hiện tại"})).status_code == 200
                assert (await day.get(f"{requests}/context")).json()["can_submit"] is False
                assert (await post(day, requests, request("Đã đổi bị động"))).status_code == 403

                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                assert (await post(day, requests, request("Sai pha sau đổi role"))).status_code == 403
                assert (await post(night, requests, request("Soi hai người rồi đoán số lượng"))).status_code == 200
                assert (await post(host, f"{actions}/{night_id}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill", "reason": "Thử quyền khi chết"})).status_code == 200
                assert (await post(night, requests, request("Chết vẫn xin"))).status_code == 403
                assert (await post(host, f"{actions}/{night_id}/actions", {
                    "client_request_id": str(uuid4()), "action": "change_role",
                    "role_id": roles["Dùng đêm khi chết"], "reason": "Thử quyền mới"})).status_code == 200
                assert (await night.get(f"{requests}/context")).json()["can_submit"] is True
                assert (await post(night, requests, request("Sau khi chết vẫn xin được"))).status_code == 200
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())
