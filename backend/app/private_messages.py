"""Durable moderator-to-player information, visible only to its recipient and host."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access, require_room_host
from app.db.events import append_event
from app.db.models import ContentRevision, GameEvent, PlayerState, PrivateMessage, User
from app.db.session import get_db
from app.rooms import locked_host_room

router = APIRouter(prefix="/api/rooms/{room_id}/game", tags=["game"])


class PrivateMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def valid_text(self):
        self.text = self.text.strip()
        self.reason = self.reason.strip() if self.reason is not None else None
        if not self.text:
            raise ValueError("Cần nội dung gửi tin")
        return self


class PrivateMessageOutput(BaseModel):
    id: UUID
    event_id: UUID
    sender_user_id: UUID
    text: str
    created_at: datetime
    edited_at: datetime | None


class SendOutput(BaseModel):
    event_id: UUID
    repeated: bool


async def messages_for_player(db: AsyncSession, room_id: UUID,
                              player_id: UUID) -> list[PrivateMessageOutput]:
    if await db.get(PlayerState, (room_id, player_id)) is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    rows = (await db.execute(select(PrivateMessage, ContentRevision).outerjoin(
        ContentRevision, ContentRevision.source_event_id == PrivateMessage.event_id
    ).where(PrivateMessage.room_id == room_id, PrivateMessage.user_id == player_id,
            ContentRevision.deleted_at.is_(None))
        .order_by(PrivateMessage.created_at, PrivateMessage.id))).all()
    return [PrivateMessageOutput(id=message.id, event_id=message.event_id,
                                 sender_user_id=message.sender_user_id,
                                 text=revision.current_text if revision and revision.edited_at
                                 else message.text, created_at=message.created_at,
                                 edited_at=revision.edited_at if revision else None)
            for message, revision in rows]


@router.get("/private-messages", response_model=list[PrivateMessageOutput])
async def my_private_messages(room_id: UUID, user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_db)) -> list[PrivateMessageOutput]:
    if await require_room_access(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="Quản trò mở tin riêng từ bảng người chơi")
    return await messages_for_player(db, room_id, user.id)


@router.get("/players/{player_id}/private-messages", response_model=list[PrivateMessageOutput])
async def host_private_messages(room_id: UUID, player_id: UUID,
                                user: User = Depends(current_user),
                                db: AsyncSession = Depends(get_db)) -> list[PrivateMessageOutput]:
    await require_room_host(db, room_id, user.id)
    return await messages_for_player(db, room_id, player_id)


@router.post("/players/{player_id}/private-messages", response_model=SendOutput)
async def send_private_message(room_id: UUID, player_id: UUID, body: PrivateMessageInput,
                               user: User = Depends(current_user),
                               db: AsyncSession = Depends(get_db)) -> SendOutput:
    room = await locked_host_room(db, room_id, user.id)
    payload = {"recipient_user_id": str(player_id), "text": body.text}
    if body.reason:
        payload["reason"] = body.reason
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "player.private_message_sent" or prior.payload != payload:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return SendOutput(event_id=prior.id, repeated=True)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    if await db.get(PlayerState, (room_id, player_id)) is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    event = await append_event(db, room_id, user.id, "player.private_message_sent", "host",
                               payload, client_request_id=body.client_request_id)
    db.add(PrivateMessage(room_id=room_id, user_id=player_id, sender_user_id=user.id,
                          event_id=event.id, text=body.text))
    await db.commit()
    return SendOutput(event_id=event.id, repeated=False)
