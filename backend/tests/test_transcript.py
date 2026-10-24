"""The postgame transcript exposes the immutable journal only to participants."""

import asyncio
import csv
import json
from contextlib import AsyncExitStack
from io import StringIO
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.db.models import GameEvent, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def command(**values):
    return {"client_request_id": str(uuid4()), **values}


def test_postgame_transcript_permissions_timeline_filters_and_exports() -> None:
    async def run() -> None:
        room_id = None
        ids = []
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, first, second, former, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(5)]
            try:
                for client, name in ((host, "Quản trò"), (first, "Người A"), (second, "Người B"),
                                     (former, "Người từng ở phòng"), (outsider, "Người ngoài")):
                    ids.append((await post(client, "/api/auth/guest", {"display_name": name})).json()["id"])
                room = (await post(host, "/api/rooms", {
                    "name": "Nhật ký một ván", "seat_limit": 3})).json()
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                transcript = f"{base}/transcript"
                for client in (first, second, former):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                assert (await post(former, f"{base}/leave")).status_code == 204
                assert (await former.get(base)).status_code == 403
                assert (await former.get(transcript)).status_code == 403
                assert (await outsider.get(transcript)).status_code == 403

                roles = []
                for name, kind, phases in (("Sói tiên tri", "active", ["day"]),
                                           ("Sói bảo vệ", "passive", [])):
                    role = await post(host, f"{base}/roles", {
                        "name": name, "description": f"Mô tả {name}", "faction_label": "Sói",
                        "seer_result": "Ác", "team_chat_label": "Bầy sói",
                        "role_kind": kind, "request_phases": phases})
                    assert role.status_code == 201
                    roles.append(role.json())
                assert (await host.patch(f"{base}/settings", json={
                    "name": room["name"], "seat_limit": 3, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                started = await post(host, f"{base}/roles/draft/start", {
                    "candidate_role_ids": [role["id"] for role in roles]})
                assert started.status_code == 200
                by_id = {ids[1]: first, ids[2]: second}
                for role in roles:
                    status = (await host.get(f"{base}/roles/draft/status")).json()
                    current = status["host_turns"][status["current_turn"] - 1]
                    assert role["id"] in current["offers"]
                    assert (await post(by_id[current["user_id"]], f"{base}/roles/draft/choose", {
                        "role_id": role["id"]})).status_code == 200
                assigned = (await host.get(f"{base}/roles/assignments")).json()
                active_id = next(item["user_id"] for item in assigned if item["role"]["role_kind"] == "active")
                active = by_id[active_id]
                passive_id = next(user_id for user_id in by_id if user_id != active_id)
                passive = by_id[passive_id]
                assert (await post(host, f"{base}/game/start")).status_code == 200
                for client in (host, active, passive):
                    assert (await client.get(transcript)).status_code == 409
                    assert (await client.get(f"{transcript}/export?format=json")).status_code == 409

                request = await post(active, f"{base}/role-requests", command(text="Đoán số lượng 2"))
                assert request.status_code == 200
                action = await post(host, f"{base}/game/players/{passive_id}/actions", command(
                    action="add_effect", text="Đã soi", reason="Theo yêu cầu"))
                assert action.status_code == 200
                noted = await post(host, f"{base}/game/players/{passive_id}/actions", command(
                    action="note", text="Ghi chú kín ban đầu"))
                assert noted.status_code == 200
                note_id = (await host.get(f"{base}/game/players/{passive_id}/notes")).json()[0]["id"]
                assert (await post(host, f"{base}/game/content/notes/{note_id}/revisions", command(
                    action="edit", text="Ghi chú kín đã sửa"))).status_code == 200
                assert (await post(host, f"{base}/role-requests/{request.json()['id']}/review", command(
                    status="accepted", reply_text="Đã xác nhận", linked_event_ids=[action.json()["event_id"]]
                ))).status_code == 200
                assert (await post(passive, f"{base}/chat/messages", command(
                    channel="common", text="Họp làng ngày 0"))).status_code == 200
                private = await post(host, f"{base}/chat/messages", command(
                    channel="host", participant_user_id=active_id, text="Tin riêng của quản trò"))
                assert private.status_code == 200
                assert (await post(host, f"{base}/game/content/{private.json()['event_id']}/revisions", command(
                    action="delete"))).status_code == 200

                opened = await post(host, f"{base}/votes", command())
                vote_id = opened.json()["vote_round_id"]
                assert (await post(active, f"{base}/votes/{vote_id}/ballots", command(
                    choice="target", target_user_id=passive_id))).status_code == 200
                assert (await post(host, f"{base}/votes/{vote_id}/close", command())).status_code == 200
                decided = await post(host, f"{base}/votes/{vote_id}/decision", command(
                    decision_text="Không loại ai", reason="Quản trò xét role"))
                assert decided.status_code == 200
                assert (await post(host, f"{base}/votes/{vote_id}/publication", command(
                    include_result=True, include_voter_names=False, include_reason=False))).status_code == 200
                notice = await post(host, f"{base}/game/events/announcements", command(
                    text="Một đòn tấn công đã xảy ra.", kind="attack",
                    linked_event_id=action.json()["event_id"]))
                assert notice.status_code == 200
                assert (await post(host, f"{base}/game/content/{notice.json()['event_id']}/revisions", command(
                    action="edit", text="Một đòn tấn công đã được xác nhận."))).status_code == 200
                assert (await post(host, f"{base}/game/players/{passive_id}/actions", command(
                    action="kill", reason="Giữ kín cái chết"))).status_code == 200
                assert (await post(host, f"{base}/game/players/{passive_id}/publication-disposition", command(
                    decision="hold", reason="Chờ xử lý role"))).status_code == 200
                assert (await post(host, f"{base}/game/players/{passive_id}/publication-disposition", command(
                    decision="skip", reason="Role che thông báo"))).status_code == 200
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                assert (await post(active, f"{base}/chat/messages", command(
                    channel="team", text="Tin đội trong đêm"))).status_code == 200
                finished = await post(host, f"{base}/game/finish", command(
                    result_label="Bầy sói thắng", result_reason="Quản trò chốt thủ công"))
                assert finished.status_code == 200

                assert (await outsider.get(transcript)).status_code == 403
                assert (await outsider.get(f"{transcript}/export?format=csv")).status_code == 403
                assert (await former.get(base)).status_code == 200
                assert room_id in [item["id"] for item in (await former.get("/api/rooms/mine")).json()]
                assert (await former.get(transcript)).status_code == 200
                assert (await active.get(transcript)).status_code == 200

                first_page = (await passive.get(f"{transcript}?limit=7")).json()
                assert first_page["info"]["result_label"] == "Bầy sói thắng"
                assert "không tự chứng minh" in first_page["info"]["limitation"]
                assert first_page["info"]["roles"][roles[0]["id"]] == roles[0]["name"]
                events = []
                cursor = 0
                while True:
                    page = (await passive.get(f"{transcript}?limit=7&after_sequence={cursor}")).json()
                    events.extend(page["events"])
                    if page["next_after_sequence"] is None:
                        assert len(events) == page["total"]
                        break
                    cursor = page["next_after_sequence"]
                sequences = [item["sequence"] for item in events]
                assert sequences == sorted(set(sequences))
                offered = next(item for item in events if item["kind"] == "roles.draft_started")
                assert offered["references"][roles[0]["id"]] == roles[0]["name"]
                kinds = {item["kind"] for item in events}
                assert {"roles.draft_started", "roles.draft_offered", "roles.draft_chosen", "chat.sent", "chat.revised",
                        "role.request_submitted", "role.request_reviewed", "vote.ballot_submitted",
                        "vote.decided", "player.kill", "player.publication_disposition_decided",
                        "event.custom_announcement", "content.revision_decided", "game.finished",
                        "player.note", "content.note_revision_decided"} <= kinds
                secret_chat = next(item for item in events if item["id"] == private.json()["event_id"])
                assert secret_chat["payload"]["request"]["text"] == "Tin riêng của quản trò"
                assert secret_chat["phase_type"] == "day" and secret_chat["round_number"] == 0
                team_chat = next(item for item in events if item["kind"] == "chat.sent"
                                 and item["payload"]["channel"] == "team")
                assert (team_chat["phase_type"], team_chat["round_number"]) == ("night", 1)
                effect = next(item for item in events if item["id"] == action.json()["event_id"])
                assert effect["before"] is not None and effect["after"] is not None
                assert effect["reason"] == "Theo yêu cầu" and effect["subject_user_id"] == passive_id
                assert effect["client_request_id"]
                assert next(item for item in events if item["kind"] == "content.note_revision_decided")["before"] == "Ghi chú kín ban đầu"
                reviewed = next(item for item in events if item["kind"] == "role.request_reviewed")
                assert action.json()["event_id"] in reviewed["related_event_ids"]
                assert next(item for item in events if item["kind"] == "game.finished")["reason"] == "Quản trò chốt thủ công"
                assert all(item["created_at"].endswith("Z") for item in events)

                chats = (await passive.get(f"{transcript}?category=chat&q=Tin")).json()
                assert chats["total"] >= 2 and all(item["kind"].startswith("chat.") for item in chats["events"])
                found = (await passive.get(f"{transcript}?q=Đoán số lượng 2")).json()
                assert any(item["kind"] == "role.request_submitted" for item in found["events"])
                linked = (await passive.get(f"{transcript}?q={action.json()['event_id']}")).json()
                assert any(item["id"] == action.json()["event_id"] for item in linked["events"])
                assert (await passive.get(f"{transcript}?limit=101")).status_code == 422

                json_one = await passive.get(f"{transcript}/export?format=json")
                json_two = await passive.get(f"{transcript}/export?format=json")
                csv_one = await passive.get(f"{transcript}/export?format=csv")
                csv_two = await passive.get(f"{transcript}/export?format=csv")
                assert json_one.status_code == csv_one.status_code == 200
                assert json_one.content == json_two.content and csv_one.content == csv_two.content
                assert json_one.headers["cache-control"] == "private, no-store"
                assert [item["id"] for item in json_one.json()["events"]] == [item["id"] for item in events]
                csv_rows = list(csv.DictReader(StringIO(csv_one.text)))
                assert [int(item["sequence"]) for item in csv_rows] == sequences
                assert json.loads(next(item for item in csv_rows if item["id"] == offered["id"])["references"])[roles[0]["id"]] == roles[0]["name"]
                assert json.loads(next(item for item in csv_rows if item["id"] == secret_chat["id"])["payload"])["request"]["text"] == "Tin riêng của quản trò"

                correction = await post(host, f"{base}/game/events/corrections", command(
                    source_event_id=action.json()["event_id"], reason="Sửa lời giải thích",
                    corrected_text="Hiệu ứng do quản trò xác nhận"))
                assert correction.status_code == 200
                newer = (await passive.get(f"{transcript}?after_sequence={sequences[-1]}")).json()
                assert [item["kind"] for item in newer["events"]] == ["event.correction_decided"]
                assert newer["events"][0]["reason"] == "Sửa lời giải thích"
                async with session_factory()() as db:
                    original = await db.get(GameEvent, UUID(action.json()["event_id"]))
                    assert original.payload == effect["payload"]
                    all_ids = (await db.scalars(select(GameEvent.id).where(
                        GameEvent.room_id == UUID(room_id)).order_by(GameEvent.sequence))).all()
                    assert len(all_ids) == len(events) + 1
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())
