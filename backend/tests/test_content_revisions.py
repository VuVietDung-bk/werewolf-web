"""Moderator edit/delete changes live content but keeps original events for audit."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

import app.content_revisions as content_revisions
from app.db.models import ContentRevision, GameEvent, Outbox, PlayerNote, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_private_message_and_death_notice_edit_delete(monkeypatch) -> None:
    async def run() -> None:
        user_ids: list[str] = []
        room_id: str | None = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            host, target, other, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(4)]
            try:
                for client, name in ((host, "Quản trò sửa nội dung"), (target, "Người nhận"),
                                     (other, "Người cùng phòng"), (outsider, "Người ngoài")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {
                    "name": "Thử sửa và xóa", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                for client in (target, other):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{base}/roles", {
                    "name": "Dân", "description": "Thử nghiệm", "faction_label": "Làng",
                    "seer_result": "Thiện", "role_kind": "passive", "quantity": 2})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Thử sửa và xóa", "seat_limit": 2, "registration_open": False,
                }, headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200

                message_url = f"{base}/game/players/{user_ids[1]}/private-messages"
                sent = await post(host, message_url, {"client_request_id": str(uuid4()),
                    "text": "Bản nhắn đầu tiên"})
                assert sent.status_code == 200
                source_id = sent.json()["event_id"]
                async with session_factory()() as db:
                    assert "reason" not in (await db.get(GameEvent, UUID(source_id))).payload
                revision_url = f"{base}/game/content/{source_id}/revisions"
                assert (await post(target, revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Người chơi sửa trái phép"})).status_code == 403
                assert (await post(outsider, revision_url, {"client_request_id": str(uuid4()),
                    "action": "delete"})).status_code == 403
                inbox_url = f"{base}/game/private-messages"
                assert (await target.get(inbox_url)).json()[0]["text"] == "Bản nhắn đầu tiên"
                assert (await other.get(inbox_url)).json() == []
                original_append = content_revisions.append_event

                async def fail_append(*args, **kwargs):
                    raise RuntimeError("simulate revision event failure")

                monkeypatch.setattr(content_revisions, "append_event", fail_append)
                assert (await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Không được lưu"})).status_code == 500
                monkeypatch.setattr(content_revisions, "append_event", original_append)
                assert (await target.get(inbox_url)).json()[0]["text"] == "Bản nhắn đầu tiên"
                async with session_factory()() as db:
                    assert await db.get(ContentRevision, UUID(source_id)) is None
                edit_body = {"client_request_id": str(uuid4()), "action": "edit",
                             "text": "  Bản nhắn đã sửa  "}
                edited = await post(host, revision_url, edit_body)
                assert edited.status_code == 200 and edited.json()["public_event_id"] is None
                assert (await post(host, revision_url, edit_body)).json() == {
                    **edited.json(), "repeated": True}
                assert (await post(host, revision_url, {**edit_body,
                    "text": "Nội dung khác"})).status_code == 409
                inbox = (await target.get(inbox_url)).json()
                assert len(inbox) == 1 and inbox[0]["text"] == "Bản nhắn đã sửa"
                assert inbox[0]["edited_at"] and inbox[0]["event_id"] == source_id
                assert (await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "   "})).status_code == 422
                deleted = await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "delete"})
                assert deleted.status_code == 200 and deleted.json()["public_event_id"] is None
                assert (await target.get(inbox_url)).json() == []
                assert (await host.get(message_url)).json() == []
                assert (await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Khôi phục"})).status_code == 409

                target_actions = f"{base}/game/players/{user_ids[1]}/actions"
                noted = await post(host, target_actions, {"client_request_id": str(uuid4()),
                    "action": "note", "text": "Ghi chú ban đầu"})
                assert noted.status_code == 200
                notes_url = f"{base}/game/players/{user_ids[1]}/notes"
                note = (await host.get(notes_url)).json()[0]
                note_revision_url = f"{base}/game/content/notes/{note['id']}/revisions"
                assert (await post(target, note_revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Sửa trái phép"})).status_code == 403
                assert (await target.get(notes_url)).status_code == 403
                assert (await post(host, note_revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "  "})).status_code == 422
                monkeypatch.setattr(content_revisions, "append_event", fail_append)
                assert (await post(host, note_revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Không được lưu"})).status_code == 500
                monkeypatch.setattr(content_revisions, "append_event", original_append)
                assert (await host.get(notes_url)).json()[0]["text"] == "Ghi chú ban đầu"
                note_edit_body = {"client_request_id": str(uuid4()), "action": "edit",
                                  "text": "  Ghi chú đã sửa  "}
                note_edit = await post(host, note_revision_url, note_edit_body)
                assert note_edit.status_code == 200 and note_edit.json()["public_event_id"] is None
                assert (await post(host, note_revision_url, note_edit_body)).json()["repeated"]
                assert (await post(host, note_revision_url, {**note_edit_body,
                    "text": "Khác"})).status_code == 409
                updated_note = (await host.get(notes_url)).json()[0]
                assert updated_note["text"] == "Ghi chú đã sửa" and updated_note["edited_at"]
                board = (await host.get(f"{base}/game/players")).json()
                assert next(item for item in board if item["user_id"] == user_ids[1])["note_count"] == 1
                note_delete = await post(host, note_revision_url, {"client_request_id": str(uuid4()),
                    "action": "delete"})
                assert note_delete.status_code == 200
                assert (await host.get(notes_url)).json() == []
                board = (await host.get(f"{base}/game/players")).json()
                assert next(item for item in board if item["user_id"] == user_ids[1])["note_count"] == 0
                assert (await post(host, note_revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Khôi phục"})).status_code == 409

                assert (await post(host, target_actions, {"client_request_id": str(uuid4()),
                    "action": "kill", "reason": "Diễn biến công khai"})).status_code == 200
                announcement = await post(host, f"{base}/game/players/{user_ids[1]}/announce", {
                    "client_request_id": str(uuid4()), "moderator_reason": "Đã xác nhận",
                    "public_cause": "Bản nguyên nhân đầu", "reveal_role": False})
                assert announcement.status_code == 200
                notices_url = f"{base}/game/death-notices"
                notice = (await other.get(notices_url)).json()[0]
                notice_revision_url = f"{base}/game/content/{notice['id']}/revisions"
                public_version = (await other.get(base)).json()["version"]
                notice_edit = await post(host, notice_revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Nguyên nhân đã sửa"})
                assert notice_edit.status_code == 200 and notice_edit.json()["public_event_id"]
                revised_notice = (await other.get(notices_url)).json()[0]
                assert revised_notice["public_cause"] == "Nguyên nhân đã sửa"
                assert revised_notice["edited_at"]
                assert (await other.get(base)).json()["version"] == public_version + 1
                notice_delete = await post(host, notice_revision_url, {
                    "client_request_id": str(uuid4()), "action": "delete"})
                assert notice_delete.status_code == 200 and notice_delete.json()["public_event_id"]
                assert (await other.get(notices_url)).json() == []
                member = next(item for item in (await other.get(base)).json()["members"]
                              if item["user_id"] == user_ids[1])
                assert not member["public_is_alive"]
                assert (await post(host, f"{base}/game/finish", {
                    "result_label": "Kết thúc", "result_reason": "Xong thử nghiệm"})).status_code == 200
                assert (await post(host, notice_revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Sau trận"})).status_code == 409
                assert (await post(host, revision_url, edit_body)).json()["repeated"]
                assert (await post(host, note_revision_url, {"client_request_id": str(uuid4()),
                    "action": "delete"})).status_code == 409

                async with session_factory()() as db:
                    original = await db.get(GameEvent, UUID(source_id))
                    assert original.payload["text"] == "Bản nhắn đầu tiên"
                    original_notice = await db.get(GameEvent, UUID(notice["id"]))
                    assert original_notice.payload["public_cause"] == "Bản nguyên nhân đầu"
                    assert (await db.get(ContentRevision, UUID(source_id))).deleted_at
                    assert (await db.get(ContentRevision, UUID(notice["id"]))).deleted_at
                    saved_note = await db.get(PlayerNote, UUID(note["id"]))
                    assert saved_note.text == "Ghi chú ban đầu" and saved_note.deleted_at
                    original_note_event = await db.get(GameEvent, UUID(noted.json()["event_id"]))
                    assert original_note_event.payload["request"]["text"] == "Ghi chú ban đầu"
                    for event_id in (edited.json()["event_id"], deleted.json()["event_id"],
                                     note_edit.json()["event_id"], note_delete.json()["event_id"],
                                     notice_edit.json()["event_id"], notice_edit.json()["public_event_id"],
                                     notice_delete.json()["event_id"], notice_delete.json()["public_event_id"]):
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
