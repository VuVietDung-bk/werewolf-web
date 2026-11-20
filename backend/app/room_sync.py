"""Bounded HTTP recovery feed for the forthcoming shared room WebSocket."""
import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access
from app.chat_cursors import cursor_codec, resync_required
from app.chat_timeline import only_queries, utc
from app.db.models import Room, RoomDelivery, RoomDeliveryStream, RoomMember, User
from app.db.session import get_db

router = APIRouter(prefix='/api/v2/rooms/{room_id}', tags=['room sync'])
LIVE_SCOPE = UUID(int=0)


class Invalidation(BaseModel):
    id: UUID
    scopes: list[str]
    channel_ids: list[UUID]


class SyncPage(BaseModel):
    deliveries: list[Invalidation]
    next_cursor: str
    has_more: bool


async def delivery_scope(db: AsyncSession, room_id: UUID, user: User):
    host = await require_room_access(db, room_id, user.id)
    room = await db.get(Room, room_id)
    member = None if host else await db.get(RoomMember, (room_id, user.id))
    stream = await db.get(RoomDeliveryStream, (room_id, user.id))
    head = stream.position if stream else 0
    return head, delivery_epoch(room, member, stream)


def delivery_epoch(room: Room, member: RoomMember | None, stream: RoomDeliveryStream | None) -> bytes:
    # No global room version or another player's private state enters the epoch.
    return hashlib.sha256(repr((room.status == 'finished',
        utc(member.joined_at).isoformat() if member else None,
        member.consent_version if member else None, stream.policy_epoch if stream else 1)).encode()).digest()[:16]


@router.get('/sync', response_model=SyncPage)
async def room_sync(room_id: UUID, request: Request, cursor: str | None = None,
                    limit: int = Query(default=50, ge=1, le=100),
                    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    only_queries(request, {'cursor', 'limit'})
    head, epoch = await delivery_scope(db, room_id, user)
    codec = cursor_codec()
    if cursor is None:
        # Establish the replay boundary before fetching UI snapshots. Old data is
        # read through its own authorized timeline, not replayed as new/unread.
        return SyncPage(deliveries=[], next_cursor=codec.encode(room_id, user.id, LIVE_SCOPE,
            'room_live', epoch, created=head, updated=0), has_more=False)
    token = codec.decode(cursor, room_id, user.id, LIVE_SCOPE, 'room_live', epoch)
    if token.created > head:
        raise resync_required()
    rows = (await db.scalars(select(RoomDelivery).where(RoomDelivery.room_id == room_id,
        RoomDelivery.user_id == user.id, RoomDelivery.position > token.created,
        RoomDelivery.position <= head).order_by(RoomDelivery.position).limit(limit + 1))).all()
    more = len(rows) > limit
    page = rows[:limit]
    position = page[-1].position if more else head
    return SyncPage(deliveries=[Invalidation(id=row.id, scopes=row.scopes, channel_ids=row.channel_ids) for row in page],
        next_cursor=codec.encode(room_id, user.id, LIVE_SCOPE, 'room_live', epoch,
            created=position, updated=0, reuse=cursor), has_more=more)
