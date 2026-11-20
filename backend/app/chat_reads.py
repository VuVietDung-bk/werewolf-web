"""Private, monotonic read checkpoints for delivered chat creations only."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, and_, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access
from app.db.models import (ChannelRead, ChatMessage, ContentRevision, GameEvent, PrivateMessage, RoleRequest, Room,
                           RoomChannel, RoomDelivery, User)
from app.db.session import get_db

router = APIRouter(prefix='/api/v2/rooms/{room_id}/channels', tags=['chat reads'])


async def unread_channels(db: AsyncSession, room_id: UUID, user_id: UUID) -> set[UUID]:
    # Start with the few channels, and stop at the first unread creation per channel.
    # The previous UNION/DISTINCT join could multiply whole histories under stale
    # PostgreSQL statistics immediately after a bulk import/backfill.
    def unread_source(model, channel_filter):
        # A scalar lookup by the full unique source/actor key prevents a bad
        # nested-loop plan from rescanning an entire room once per delivery.
        delivered_position = select(RoomDelivery.position).where(
            RoomDelivery.room_id == room_id, RoomDelivery.user_id == user_id,
            RoomDelivery.source_event_id == model.event_id).correlate(model).scalar_subquery()
        return select(literal(1)).select_from(model).outerjoin(ContentRevision,
                ContentRevision.source_event_id == model.event_id).where(
                    model.room_id == room_id, model.sender_user_id != user_id,
                    ContentRevision.deleted_at.is_(None), channel_filter,
                    delivered_position > func.coalesce(ChannelRead.position, 0))\
            .correlate(RoomChannel, ChannelRead).exists()
    chat_match = or_(
        and_(RoomChannel.kind == 'common', ChatMessage.channel == 'common'),
        and_(RoomChannel.kind == 'team', ChatMessage.channel == 'team', ChatMessage.team_label == RoomChannel.channel_key),
        and_(RoomChannel.kind == 'moderator_dm', ChatMessage.channel == 'host',
             cast(ChatMessage.participant_user_id, String) == RoomChannel.channel_key))
    private_match = and_(RoomChannel.kind == 'moderator_dm', cast(PrivateMessage.user_id, String) == RoomChannel.channel_key)
    request_position = select(RoomDelivery.position).where(RoomDelivery.room_id == room_id,
        RoomDelivery.user_id == user_id,RoomDelivery.source_event_id == GameEvent.id).correlate(GameEvent).scalar_subquery()
    request_unread = select(literal(1)).select_from(RoleRequest).join(GameEvent,
        (GameEvent.room_id == RoleRequest.room_id) & (GameEvent.sequence == RoleRequest.updated_sequence)).where(
            RoleRequest.room_id == room_id,RoomChannel.kind == 'moderator_dm',
            cast(RoleRequest.user_id,String) == RoomChannel.channel_key,GameEvent.actor_user_id != user_id,
            request_position > func.coalesce(ChannelRead.position,0)).correlate(RoomChannel,ChannelRead).exists()
    query = select(RoomChannel.id).outerjoin(ChannelRead,
        (ChannelRead.room_id == room_id) & (ChannelRead.user_id == user_id) & (ChannelRead.channel_id == RoomChannel.id)
    ).where(RoomChannel.room_id == room_id, or_(unread_source(ChatMessage, chat_match), unread_source(PrivateMessage, private_match),request_unread))
    return set((await db.scalars(query)).all())


class ReadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    visible_item_id: str = Field(max_length=64, pattern=r'^(chat|private_message|role_request):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    read_receipt: UUID | None = None


async def advance_read(db, room_id, user_id, channel_id, position):
    statement = insert(ChannelRead).values(room_id=room_id, user_id=user_id, channel_id=channel_id, position=position)
    await db.execute(statement.on_conflict_do_update(
        index_elements=[ChannelRead.room_id, ChannelRead.user_id, ChannelRead.channel_id],
        set_={'position': func.greatest(ChannelRead.position, statement.excluded.position), 'updated_at': func.now()},
        where=ChannelRead.position < statement.excluded.position))


@router.post('/{channel_id}/read', status_code=204)
async def acknowledge_read(room_id: UUID, channel_id: UUID, body: ReadInput,
                           user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.channels import resolve_channel
    ref = await resolve_channel(db, room_id, channel_id, user)
    if ref.kind not in {'common', 'team', 'moderator_dm'}:
        raise HTTPException(status_code=409, detail='Kênh này dùng công cụ riêng')
    kind, source_id = body.visible_item_id.split(':')
    source = await db.get({'chat':ChatMessage,'private_message':PrivateMessage,'role_request':RoleRequest}[kind], UUID(source_id))
    room = await db.get(Room, room_id)
    host, finished = room.owner_user_id == user.id, room.status == 'finished'
    valid = source is not None and source.room_id == room_id
    if valid and kind == 'chat':
        valid = (source.channel == ('host' if ref.kind == 'moderator_dm' else ref.kind)
                 and source.team_label == ref.team_label and source.participant_user_id == ref.participant_user_id)
        if valid and ref.kind == 'team' and not (host or finished):
            valid = str(user.id) in source.recipient_ids
    elif valid:
        valid = ref.kind == 'moderator_dm' and source.user_id == ref.participant_user_id
    if not valid:
        raise HTTPException(status_code=404, detail='Không tìm thấy tin được phép xem trong kênh')
    # Expanded postgame snapshots are history, not new delivery notifications.
    if finished:
        return Response(status_code=204)
    revision = await db.get(ContentRevision, source.event_id)
    if revision is not None and revision.deleted_at is not None:
        raise HTTPException(status_code=409, detail='Tin này đã bị xóa; hãy đồng bộ lại kênh')
    if kind == 'role_request':
        if body.read_receipt is None:
            raise HTTPException(status_code=409,detail='Hãy đồng bộ lại trạng thái yêu cầu trước khi xác nhận đã đọc')
        position=await db.scalar(select(RoomDelivery.position).join(GameEvent,GameEvent.id == RoomDelivery.source_event_id).where(
            RoomDelivery.id == body.read_receipt,RoomDelivery.room_id == room_id,RoomDelivery.user_id == user.id,
            GameEvent.room_id == room_id,or_(GameEvent.id == source.event_id,and_(
                GameEvent.kind.in_(['role.request_reviewed','role.request_withdrawn']),
                GameEvent.payload['request']['request_id'].astext == str(source.id)))))
        if position is None:raise HTTPException(status_code=404,detail='Không tìm thấy xác nhận của yêu cầu này')
    else:
        if body.read_receipt is not None:raise HTTPException(status_code=422,detail='Tin chat không dùng xác nhận phiên bản yêu cầu')
        position = await db.scalar(select(RoomDelivery.position).where(RoomDelivery.room_id == room_id,
            RoomDelivery.user_id == user.id, RoomDelivery.source_event_id == source.event_id))
    if position is None:
        # Public history from before joining may be readable without being delivered.
        return Response(status_code=204)
    await advance_read(db, room_id, user.id, channel_id, position)
    # Recheck after a possible wait on another tab's upsert; no room-wide write lock.
    await db.refresh(room)
    if not host:
        from app.db.models import RoomMember
        member = await db.get(RoomMember, (room_id, user.id), populate_existing=True)
        if member is not None:
            await db.refresh(member)
    await require_room_access(db, room_id, user.id)
    await db.commit()
    return Response(status_code=204)
