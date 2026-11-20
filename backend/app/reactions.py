"""Authorized, idempotent reactions; source revisions drive the existing changes feed."""
from datetime import timedelta
from math import ceil
import unicodedata
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, now_utc, require_room_access
from app.db.events import append_event
from app.db.models import (ChannelRead, ChatMessage, ContentRevision, GameEvent, MessageReaction,
                           PrivateMessage, Room, RoomDelivery, RoomMember, User)
from app.db.session import get_db

router = APIRouter(prefix='/api/v2/rooms/{room_id}/channels/{channel_id}/items', tags=['reactions'])
# Same bounded native Unicode choices as the picker. Composite emoji stay whole.
ALLOWED_REACTIONS = frozenset([
    '🙂','😀','😂','🤣','😊','😎','🥰','😍','😢','😭','😱','😴','🤔','🧐','😅','😐','🙃','🤫','🤐','👀',
    '👍','👎','👏','🙏','🤝','👋','💪','✌️','❤️','💜','💔','🔥','✨','🎉','✅','❌','❓','⚠️',
    '🐺','🌙','☀️','🕯️','🔮','🧪','🛡️','🏹','👻','💀','🎲','👨‍👩‍👧‍👦',
])
MAX_PER_ACTOR = 8
MAX_PER_SOURCE = 20
BURST_LIMIT = 6
MINUTE_LIMIT = 30


class ReactionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: UUID
    emoji: str = Field(min_length=1, max_length=32)

    @field_validator('emoji')
    @classmethod
    def valid_emoji(cls, value):
        value = unicodedata.normalize('NFC', value)
        if value not in ALLOWED_REACTIONS:
            raise ValueError('Chọn một emoji trong bộ chọn reaction')
        return value


class ReactionAck(BaseModel):
    emoji: str
    present: bool


async def check_rate(db, room_id, user_id):
    now = now_utc()
    for limit, seconds in ((BURST_LIMIT, 10), (MINUTE_LIMIT, 60)):
        dates = (await db.scalars(select(GameEvent.created_at).where(GameEvent.room_id == room_id,
            GameEvent.actor_user_id == user_id, GameEvent.kind == 'chat.reaction_decided',
            GameEvent.created_at > now - timedelta(seconds=seconds))
            .order_by(GameEvent.created_at.desc()).limit(limit))).all()
        if len(dates) >= limit:
            wait = max(1, ceil((dates[-1] + timedelta(seconds=seconds) - now).total_seconds()))
            raise HTTPException(status_code=429, detail=f'Bạn thao tác reaction quá nhanh. Thử lại sau {wait} giây.',
                headers={'Retry-After': str(wait)})


async def apply_reaction(room_id, channel_id, source_kind, source_id, body, present, user, db):
    from app.channels import resolve_channel
    await require_room_access(db, room_id, user.id)
    room = await db.scalar(select(Room).where(Room.id == room_id).with_for_update().execution_options(populate_existing=True))
    if room is None:
        raise HTTPException(status_code=404, detail='Không tìm thấy phòng')
    if room.owner_user_id != user.id:
        await db.get(RoomMember, (room_id, user.id), populate_existing=True)
    await require_room_access(db, room_id, user.id)
    ref = await resolve_channel(db, room_id, channel_id, user)
    request = {'source_kind': source_kind, 'source_id': str(source_id), 'channel_id': str(channel_id),
               'emoji': body.emoji, 'present': present}
    prior = await db.scalar(select(GameEvent).where(GameEvent.room_id == room_id,
        GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != 'chat.reaction_decided' or prior.actor_user_id != user.id or prior.payload.get('request') != request:
            raise HTTPException(status_code=409, detail='Mã yêu cầu đã dùng cho thao tác khác')
        return {'emoji': body.emoji, 'present': present}
    if source_kind not in {'chat', 'private_message'}:
        raise HTTPException(status_code=404, detail='Không tìm thấy tin được phép xem trong kênh')
    source = await db.get(ChatMessage if source_kind == 'chat' else PrivateMessage, source_id, populate_existing=True)
    valid = source is not None and source.room_id == room_id
    if valid and source_kind == 'chat':
        valid = (source.channel == ('host' if ref.kind == 'moderator_dm' else ref.kind)
                 and source.team_label == ref.team_label and source.participant_user_id == ref.participant_user_id)
        if valid and ref.kind == 'team' and room.owner_user_id != user.id:
            valid = str(user.id) in source.recipient_ids
    elif valid:
        valid = ref.kind == 'moderator_dm' and source.user_id == ref.participant_user_id
    if not valid:
        raise HTTPException(status_code=404, detail='Không tìm thấy tin được phép xem trong kênh')
    revision = await db.get(ContentRevision, source.event_id)
    if revision is not None and revision.deleted_at is not None:
        raise HTTPException(status_code=409, detail='Tin nhắn đã bị xóa')
    if not ref.capabilities.react:
        raise HTTPException(status_code=403, detail=ref.readonly_reason or 'Không thể dùng reaction trong kênh lúc này')
    position = await db.scalar(select(RoomDelivery.position).where(RoomDelivery.room_id == room_id,
        RoomDelivery.user_id == user.id, RoomDelivery.source_event_id == source.event_id))
    read = await db.get(ChannelRead, (room_id, user.id, channel_id))
    if position is not None and (read is None or read.position < position):
        raise HTTPException(status_code=409, detail='Hãy mở và đọc tin nhắn trước khi dùng reaction')
    await check_rate(db, room_id, user.id)
    existing = await db.get(MessageReaction, (source.event_id, user.id, body.emoji))
    previous = existing is not None
    if present and not previous:
        own = await db.scalar(select(func.count()).select_from(MessageReaction).where(
            MessageReaction.source_event_id == source.event_id, MessageReaction.user_id == user.id))
        distinct = await db.scalar(select(func.count(func.distinct(MessageReaction.emoji))).where(
            MessageReaction.source_event_id == source.event_id))
        exists_emoji = await db.scalar(select(MessageReaction.user_id).where(
            MessageReaction.source_event_id == source.event_id, MessageReaction.emoji == body.emoji).limit(1))
        if own >= MAX_PER_ACTOR:
            raise HTTPException(status_code=409, detail=f'Bạn đã chọn tối đa {MAX_PER_ACTOR} emoji cho tin này; hãy gỡ bớt trước khi thêm')
        if distinct >= MAX_PER_SOURCE and exists_emoji is None:
            raise HTTPException(status_code=409, detail=f'Tin đã có {MAX_PER_SOURCE} loại emoji; hãy chọn một loại đã có')
        db.add(MessageReaction(room_id=room_id, source_event_id=source.event_id, user_id=user.id, emoji=body.emoji))
    elif previous and not present:
        await db.delete(existing)
    decision_time = now_utc()
    decision = await append_event(db, room_id, user.id, 'chat.reaction_decided', 'host',
        {'request': request, 'source_event_id': str(source.event_id), 'previous': previous, 'current': present,
         'changed': previous != present}, client_request_id=body.client_request_id)
    # DB now() is the transaction start; time spent waiting for the room lock
    # must not make newly processed commands appear outside the rate window.
    decision.created_at = decision_time
    if previous != present:
        payload = {'source_event_id': str(source.event_id), 'emoji': body.emoji, 'present': present,
            'channel': source.channel if source_kind == 'chat' else 'host',
            'team_label': source.team_label if source_kind == 'chat' else None,
            'participant_user_id': str(source.participant_user_id) if source_kind == 'chat' and source.participant_user_id else str(source.user_id) if source_kind == 'private_message' else None,
            'recipient_ids': source.recipient_ids if source_kind == 'chat' else []}
        changed = await append_event(db, room_id, user.id, 'chat.reaction_changed',
            'room' if payload['channel'] == 'common' else 'host', payload)
        changed.created_at = decision_time
        source.updated_sequence = changed.sequence
        decision.payload = {**decision.payload, 'public_event_id': str(changed.id)}
    await db.commit()
    return {'emoji': body.emoji, 'present': present}


@router.put('/{source_kind}/{source_id}/reactions', response_model=ReactionAck)
async def add_reaction(room_id: UUID, channel_id: UUID, source_kind: str, source_id: UUID,
                       body: ReactionInput, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await apply_reaction(room_id, channel_id, source_kind, source_id, body, True, user, db)


@router.delete('/{source_kind}/{source_id}/reactions', response_model=ReactionAck)
async def remove_reaction(room_id: UUID, channel_id: UUID, source_kind: str, source_id: UUID,
                          body: ReactionInput, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await apply_reaction(room_id, channel_id, source_kind, source_id, body, False, user, db)
