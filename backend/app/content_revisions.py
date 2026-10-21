"""Edit or remove moderator content from live views without erasing its audit trail."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, now_utc
from app.db.events import append_event
from app.db.models import ContentRevision, GameEvent, PlayerNote, PrivateMessage, User
from app.db.session import get_db
from app.rooms import locked_host_room

router = APIRouter(prefix="/api/rooms/{room_id}/game/content", tags=["game"])
NOTICE_KINDS = {"player.death_announced", "player.revival_announced",
                "event.public_correction", "event.custom_announcement", "vote.result_announced"}


class ContentChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    action: Literal["edit", "delete"]
    text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def valid_fields(self):
        if self.action == "edit" and self.text is None:
            raise ValueError("Cần nội dung mới")
        if self.action == "delete" and self.text is not None:
            raise ValueError("Lệnh xóa không nhận nội dung mới")
        if self.text is not None:
            self.text = self.text.strip()
        return self


class ContentChangeOutput(BaseModel):
    event_id: UUID
    repeated: bool
    public_event_id: UUID | None


@router.post("/{source_event_id}/revisions", response_model=ContentChangeOutput)
async def change_content(room_id: UUID, source_event_id: UUID, body: ContentChangeInput,
                         user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)) -> ContentChangeOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"source_event_id": str(source_event_id), "action": body.action,
               "text": body.text}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "content.revision_decided" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ContentChangeOutput(event_id=prior.id, repeated=True,
                                   public_event_id=UUID(prior.payload["public_event_id"])
                                   if prior.payload.get("public_event_id") else None)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Chỉ sửa/xóa nội dung khi trận đang chơi")
    source = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.id == source_event_id))
    if source is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy nội dung trong phòng")
    is_notice = source.kind in NOTICE_KINDS and source.visibility == "room"
    is_private = source.kind == "player.private_message_sent" and source.visibility == "host"
    if not is_notice and not is_private:
        raise HTTPException(status_code=409, detail="Sự kiện này không phải tin nhắn hoặc thông báo có thể sửa")
    if is_private and await db.scalar(select(PrivateMessage.id).where(
        PrivateMessage.room_id == room_id, PrivateMessage.event_id == source_event_id)) is None:
        raise HTTPException(status_code=409, detail="Không tìm thấy tin nhắn gốc")
    if body.action == "edit":
        if is_private and not body.text:
            raise HTTPException(status_code=422, detail="Tin riêng không được để trống")
        if source.kind in {"event.public_correction", "event.custom_announcement", "vote.result_announced"} and not body.text:
            raise HTTPException(status_code=422, detail="Nội dung đính chính không được để trống")
        if source.kind in {"player.death_announced", "player.revival_announced"} and len(body.text) > 500:
            raise HTTPException(status_code=422, detail="Nguyên nhân công khai tối đa 500 ký tự")
    revision = await db.get(ContentRevision, source_event_id)
    if revision is not None and revision.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Nội dung đã bị xóa")
    original_text = ((source.payload.get("public_cause") or "")
                     if source.kind in {"player.death_announced", "player.revival_announced"}
                     else source.payload["text"])
    previous_text = revision.current_text if revision and revision.edited_at else original_text
    if body.action == "edit" and body.text == (previous_text or ""):
        raise HTTPException(status_code=409, detail="Nội dung chưa thay đổi")
    if revision is None:
        revision = ContentRevision(source_event_id=source_event_id, room_id=room_id)
        db.add(revision)
    changed_at = now_utc()
    if body.action == "edit":
        revision.current_text = body.text or None
        revision.edited_at = changed_at
    else:
        revision.deleted_at = changed_at
    decision = await append_event(db, room_id, user.id, "content.revision_decided", "host",
                                  {"request": request, "source_kind": source.kind,
                                   "previous_text": previous_text,
                                   "new_text": body.text if body.action == "edit" else None},
                                  client_request_id=body.client_request_id)
    public_event_id = None
    if is_notice:
        public_event = await append_event(db, room_id, user.id,
                                          "content.notice_edited" if body.action == "edit"
                                          else "content.notice_deleted", "room",
                                          {"source_event_id": str(source_event_id),
                                           "text": (body.text or None) if body.action == "edit"
                                           else None})
        public_event_id = public_event.id
        decision.payload = {**decision.payload, "public_event_id": str(public_event_id)}
    await db.commit()
    return ContentChangeOutput(event_id=decision.id, repeated=False,
                               public_event_id=public_event_id)


@router.post("/notes/{note_id}/revisions", response_model=ContentChangeOutput)
async def change_note(room_id: UUID, note_id: UUID, body: ContentChangeInput,
                      user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> ContentChangeOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"note_id": str(note_id), "action": body.action, "text": body.text}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "content.note_revision_decided" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ContentChangeOutput(event_id=prior.id, repeated=True, public_event_id=None)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Chỉ sửa/xóa ghi chú khi trận đang chơi")
    note = await db.get(PlayerNote, note_id)
    if note is None or note.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy ghi chú trong phòng")
    if note.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Ghi chú đã bị xóa")
    if body.action == "edit" and not body.text:
        raise HTTPException(status_code=422, detail="Ghi chú không được để trống")
    previous_text = note.current_text if note.edited_at else note.text
    if body.action == "edit" and body.text == previous_text:
        raise HTTPException(status_code=409, detail="Nội dung chưa thay đổi")
    if body.action == "edit":
        note.current_text = body.text
        note.edited_at = now_utc()
    else:
        note.deleted_at = now_utc()
    decision = await append_event(db, room_id, user.id, "content.note_revision_decided", "host",
                                  {"request": request, "player_id": str(note.user_id),
                                   "previous_text": previous_text,
                                   "new_text": body.text if body.action == "edit" else None},
                                  client_request_id=body.client_request_id)
    await db.commit()
    return ContentChangeOutput(event_id=decision.id, repeated=False, public_event_id=None)
