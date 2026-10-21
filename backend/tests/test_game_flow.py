"""Moderator phase controls persist, serialize, and leave an event trail."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.db.models import GameEvent, Outbox, PlayerNote, PlayerState, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_phase_pause_resume_and_manual_finish() -> None:
    async def run() -> None:
        ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, player_a, player_b, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(4)]
            try:
                for client, name in zip((host, player_a, player_b, outsider),
                                        ("Quản trò pha", "Người A", "Người B", "Người ngoài")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Kiểm tra pha", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                url = f"/api/rooms/{room_id}"
                for client in (player_a, player_b):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                role = {"name": "Dân làng", "description": "Thảo luận trong ngày.",
                        "faction_label": "Làng", "seer_result": "Thiện",
                        "role_kind": "passive", "quantity": 2}
                assert (await post(host, f"{url}/roles", role)).status_code == 201
                settings = {"name": "Kiểm tra pha", "seat_limit": 2, "registration_open": False}
                assert (await host.patch(f"{url}/settings", json=settings,
                                         headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{url}/roles/distribute-random")).status_code == 200
                start_body = {"client_request_id": str(uuid4())}
                assert (await post(host, f"{url}/game/start", start_body)).status_code == 200
                board_url = f"{url}/game/players"
                board = await host.get(board_url)
                assert board.status_code == 200 and len(board.json()) == 2
                assert all(item["is_alive"] and not item["effects"] for item in board.json())
                assert (await player_a.get(board_url)).status_code == 403
                assert (await outsider.get(board_url)).status_code == 403
                assert "is_alive" not in (await player_a.get(url)).json()["members"][0]
                public_version_before_kill = (await player_a.get(url)).json()["version"]
                target_id = ids[1]
                actions_url = f"{board_url}/{target_id}/actions"
                kill_id = str(uuid4())
                kill_body = {"client_request_id": kill_id, "action": "kill", "reason": "Sói tấn công"}
                assert (await post(player_a, actions_url, kill_body)).status_code == 403
                kill_attempts = await asyncio.gather(*(post(host, actions_url, kill_body)
                                                      for _ in range(2)))
                assert [response.status_code for response in kill_attempts] == [200, 200]
                assert sorted(response.json()["repeated"] for response in kill_attempts) == [False, True]
                killed = next(response for response in kill_attempts if not response.json()["repeated"])
                repeated = await post(host, actions_url, kill_body)
                assert repeated.status_code == 200 and repeated.json() == {
                    "event_id": killed.json()["event_id"], "repeated": True}
                assert (await post(host, actions_url, {**kill_body,
                    "reason": "Lý do khác"})).status_code == 409
                assert (await post(host, actions_url, {**kill_body,
                    "client_request_id": str(uuid4())})).status_code == 409
                assert not next(item for item in (await host.get(board_url)).json()
                                if item["user_id"] == target_id)["is_alive"]
                hidden_room = (await player_b.get(url)).json()
                assert hidden_room["version"] == public_version_before_kill
                assert next(item for item in hidden_room["members"]
                            if item["user_id"] == target_id)["public_is_alive"]
                notice_url = f"{url}/game/death-notices"
                assert (await player_b.get(notice_url)).json() == []
                assert (await outsider.get(notice_url)).status_code == 403
                advance = {"expected_phase_type": "day", "expected_round_number": 0}
                assert (await post(player_a, f"{url}/game/advance", advance)).status_code == 403
                assert (await post(outsider, f"{url}/game/advance", advance)).status_code == 403
                assert (await post(host, f"{url}/game/pause", {"reason": "  "})).status_code == 422
                pause_body = {"client_request_id": str(uuid4()), "reason": "  Nghỉ giải lao  "}
                paused = await post(host, f"{url}/game/pause", pause_body)
                assert paused.status_code == 200 and paused.json()["is_paused"]
                assert (await post(host, f"{url}/game/pause", pause_body)).status_code == 200
                assert (await post(host, f"{url}/game/pause", {
                    **pause_body, "reason": "Lý do khác"})).status_code == 409
                assert (await post(host, f"{url}/game/resume", {
                    "client_request_id": pause_body["client_request_id"]})).status_code == 409
                assert (await post(host, actions_url, {"client_request_id": str(uuid4()),
                    "action": "add_effect", "text": "Bị câm", "reason": "Đêm 1"})).status_code == 409
                noted = await post(host, actions_url, {"client_request_id": str(uuid4()),
                    "action": "note", "text": "  Kiểm tra quyết định  "})
                assert noted.status_code == 200
                assert (await player_b.get(f"{board_url}/{target_id}/notes")).status_code == 403
                notes = (await host.get(f"{board_url}/{target_id}/notes")).json()
                assert len(notes) == 1 and notes[0]["text"] == "Kiểm tra quyết định"
                assert (await player_a.get(url)).json()["is_paused"]
                assert (await post(host, f"{url}/game/pause", {"reason": "Lần nữa"})).status_code == 409
                assert (await post(host, f"{url}/game/advance", advance)).status_code == 409
                resume_body = {"client_request_id": str(uuid4())}
                resumed = await post(host, f"{url}/game/resume", resume_body)
                assert resumed.status_code == 200 and not resumed.json()["is_paused"]
                assert (await post(host, f"{url}/game/pause", pause_body)).json()["is_paused"] is False
                pause_again = {"client_request_id": str(uuid4()), "reason": "Dừng lần hai"}
                assert (await post(host, f"{url}/game/pause", pause_again)).json()["is_paused"]
                assert (await post(host, f"{url}/game/resume", resume_body)).json()["is_paused"]
                assert not (await post(host, f"{url}/game/resume", {
                    "client_request_id": str(uuid4())})).json()["is_paused"]
                effect = {"client_request_id": str(uuid4()), "action": "add_effect",
                          "text": "Bị câm", "reason": "Do quản trò xác nhận"}
                assert (await post(host, actions_url, effect)).status_code == 200
                assert (await post(host, actions_url, effect)).json()["repeated"]
                assert (await post(host, actions_url, {**effect,
                    "client_request_id": str(uuid4())})).status_code == 409
                assert next(item for item in (await host.get(board_url)).json()
                            if item["user_id"] == target_id)["effects"] == ["Bị câm"]
                assert (await post(host, actions_url, {"client_request_id": str(uuid4()),
                    "action": "remove_effect", "text": "bị CÂM", "reason": "Hết hiệu lực"})).status_code == 200
                assert (await post(host, actions_url, {"client_request_id": str(uuid4()),
                    "action": "remove_effect", "text": "Bị câm", "reason": "Lặp"})).status_code == 409
                assert (await post(host, f"{url}/game/resume")).status_code == 409
                attempts = await asyncio.gather(*(post(host, f"{url}/game/advance", advance)
                                                  for _ in range(2)))
                assert sorted(response.status_code for response in attempts) == [200, 409]
                assert (await host.get(url)).json()["phase_type"] == "night"
                assert (await host.get(url)).json()["round_number"] == 1
                night_advance = {"expected_phase_type": "night", "expected_round_number": 1,
                                 "client_request_id": str(uuid4())}
                night = await post(host, f"{url}/game/advance", night_advance)
                assert night.status_code == 200
                assert (night.json()["phase_type"], night.json()["round_number"]) == ("day", 1)
                assert (await post(host, f"{url}/game/advance", night_advance)).json()["round_number"] == 1
                death_body = {"client_request_id": str(uuid4()),
                              "moderator_reason": "Đã đối chiếu kết quả đêm",
                              "public_cause": "  Bị tấn công  ", "reveal_role": False}
                announce_url = f"{board_url}/{target_id}/announce"
                assert (await post(player_b, announce_url, death_body)).status_code == 403
                assert (await post(host, announce_url, {**death_body,
                    "moderator_reason": " "})).status_code == 422
                announced = await post(host, announce_url, death_body)
                assert announced.status_code == 200 and not announced.json()["repeated"]
                assert (await post(host, announce_url, death_body)).json() == {
                    "event_id": announced.json()["event_id"], "repeated": True}
                assert (await post(host, announce_url, {**death_body,
                    "public_cause": "Lý do khác"})).status_code == 409
                public_room = (await player_b.get(url)).json()
                public_member = next(item for item in public_room["members"]
                                     if item["user_id"] == target_id)
                assert not public_member["public_is_alive"]
                assert public_member["public_role_name"] is None
                assert public_room["version"] > public_version_before_kill
                notices = (await player_b.get(notice_url)).json()
                assert len(notices) == 1 and notices[0]["status"] == "dead"
                assert notices[0]["public_cause"] == "Bị tấn công"
                assert notices[0]["revealed_role_name"] is None
                assert "moderator_reason" not in notices[0]
                death_notice_id = notices[0]["id"]
                revision_url = f"{url}/game/content/{death_notice_id}/revisions"
                edit_cause = {"client_request_id": str(uuid4()), "action": "edit",
                              "text": "Không rõ nguyên nhân"}
                assert (await post(player_b, revision_url, edit_cause)).status_code == 403
                edited = await post(host, revision_url, edit_cause)
                assert edited.status_code == 200 and edited.json()["public_event_id"]
                assert (await post(host, revision_url, edit_cause)).json()["repeated"] is True
                updated_notices = (await player_b.get(notice_url)).json()
                assert len(updated_notices) == 1
                assert updated_notices[0]["public_cause"] == "Không rõ nguyên nhân"
                assert updated_notices[0]["edited_at"] is not None
                assert not next(item for item in (await player_b.get(url)).json()["members"]
                                if item["user_id"] == target_id)["public_is_alive"]
                delete_notice = {"client_request_id": str(uuid4()), "action": "delete", "text": None}
                deleted = await post(host, revision_url, delete_notice)
                assert deleted.status_code == 200 and deleted.json()["public_event_id"]
                assert (await player_b.get(notice_url)).json() == []
                assert not next(item for item in (await player_b.get(url)).json()["members"]
                                if item["user_id"] == target_id)["public_is_alive"]
                assert (await post(host, revision_url, {**delete_notice,
                    "client_request_id": str(uuid4())})).status_code == 409
                assert (await post(host, announce_url, {**death_body,
                    "client_request_id": str(uuid4())})).status_code == 409
                revived = await post(host, actions_url, {"client_request_id": str(uuid4()),
                    "action": "revive", "reason": "Sửa quyết định"})
                assert revived.status_code == 200
                assert next(item for item in (await host.get(board_url)).json()
                            if item["user_id"] == target_id)["is_alive"]
                assert not next(item for item in (await player_b.get(url)).json()["members"]
                                if item["user_id"] == target_id)["public_is_alive"]
                assert (await post(host, actions_url, {"client_request_id": str(uuid4()),
                    "action": "revive", "reason": "Lặp"})).status_code == 409
                revival = await post(host, announce_url, {"client_request_id": str(uuid4()),
                    "moderator_reason": "Xác nhận sửa sai", "reveal_role": True})
                assert revival.status_code == 200
                public_member = next(item for item in (await player_a.get(url)).json()["members"]
                                     if item["user_id"] == target_id)
                assert public_member["public_is_alive"]
                assert public_member["public_role_name"] == "Dân làng"
                notices = (await player_a.get(notice_url)).json()
                assert [item["status"] for item in notices] == ["alive"]
                assert notices[0]["revealed_role_name"] == "Dân làng"
                assert (await post(host, f"{url}/game/finish", {
                    "result_label": "  ", "result_reason": "Ván kết thúc"})).status_code == 422
                finish_body = {"client_request_id": str(uuid4()),
                    "result_label": " Phe Làng thắng ",
                    "result_reason": " Quản trò đã kiểm tra kết quả. "}
                finished = await post(host, f"{url}/game/finish", finish_body)
                assert finished.status_code == 200
                assert finished.json()["status"] == "finished"
                assert finished.json()["result_label"] == "Phe Làng thắng"
                assert finished.json()["result_reason"] == "Quản trò đã kiểm tra kết quả."
                assert finished.json()["ended_at"]
                assert (await player_b.get(url)).json()["result_label"] == "Phe Làng thắng"
                assert (await post(host, f"{url}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 1})).status_code == 409
                assert (await post(host, f"{url}/game/finish", {
                    "result_label": "Khác", "result_reason": "Không hợp lệ"})).status_code == 409
                assert (await post(host, f"{url}/game/finish", finish_body)).json()["status"] == "finished"
                assert (await post(host, f"{url}/game/start", start_body)).json()["status"] == "finished"
                assert (await post(host, actions_url, kill_body)).json() == {
                    "event_id": killed.json()["event_id"], "repeated": True}
                assert (await post(host, announce_url, death_body)).json() == {
                    "event_id": announced.json()["event_id"], "repeated": True}
                assert (await post(host, actions_url, {"client_request_id": str(uuid4()),
                    "action": "note", "text": "Ghi sau trận"})).status_code == 409
                async with session_factory()() as db:
                    events = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind.like("game.%")
                    ).order_by(GameEvent.sequence))).all()
                    assert [event.kind for event in events] == [
                        "game.started", "game.paused", "game.resumed",
                        "game.paused", "game.resumed",
                        "game.phase_advanced", "game.phase_advanced", "game.finished"]
                    assert events[1].payload["reason"] == "Nghỉ giải lao"
                    assert events[-1].payload["result_label"] == "Phe Làng thắng"
                    for event in events:
                        assert (await db.scalar(select(Outbox).where(Outbox.event_id == event.id))) is not None
                    player_events = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind.like("player.%")
                    ).order_by(GameEvent.sequence))).all()
                    assert [event.kind for event in player_events] == [
                        "player.kill", "player.note", "player.add_effect",
                        "player.remove_effect", "player.announcement_decided",
                        "player.death_announced", "player.revive",
                        "player.announcement_decided", "player.revival_announced"]
                    assert [event.visibility for event in player_events] == [
                        "host", "host", "host", "host", "host", "room",
                        "host", "host", "room"]
                    for event in player_events:
                        assert (await db.scalar(select(Outbox).where(
                            Outbox.event_id == event.id))) is not None
                    state = await db.get(PlayerState, (UUID(room_id), UUID(target_id)))
                    assert state.is_alive and state.effects == []
                    assert (await db.scalar(select(PlayerNote).where(
                        PlayerNote.room_id == UUID(room_id),
                        PlayerNote.user_id == UUID(target_id)))).text == "Kiểm tra quyết định"
            finally:
                if room_id:
                    async with session_factory()() as db:
                        room = await db.get(Room, UUID(room_id))
                        if room:
                            await db.delete(room)
                            await db.commit()
                async with session_factory()() as db:
                    for user_id in ids:
                        user = await db.get(User, UUID(user_id))
                        if user:
                            await db.delete(user)
                    await db.commit()

    asyncio.run(run())
