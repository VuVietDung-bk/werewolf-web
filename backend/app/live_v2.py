"""Room delivery replay: bounded batches, opaque cursors and authorization at send."""
import asyncio
from dataclasses import dataclass
import os
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import BigInteger, column, select, true, values
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.auth import COOKIE_NAME, POSTGAME_POLICY_VERSION, lookup_session, now_utc, origin_allowed
from app.chat_cursors import cursor_codec
from app.db.models import Room, RoomDelivery, RoomDeliveryStream, RoomMember, Session
from app.db.session import session_factory
from app.live import EVENT_BATCH_SIZE, LiveHub, RoomFeed
from app.room_sync import LIVE_SCOPE, delivery_epoch

router = APIRouter(tags=['room live v2'])
BATCH_SIZE = 50
SEND_TIMEOUT_SECONDS = 2


@dataclass(eq=False)
class DeliveryConnection:
    websocket: WebSocket
    user_id: UUID
    session_id: UUID
    cursor: int
    token: str
    epoch: bytes


def access_code(room, member, session, user_id):
    if session is None or session.user_id != user_id or session.revoked_at is not None or session.expires_at <= now_utc():
        return 4401
    if room is None:
        return 4404
    if user_id != room.owner_user_id and (member is None or member.consent_version != POSTGAME_POLICY_VERSION
            or (member.left_at is not None and room.status != 'finished')):
        return 4403
    return None


async def current_scope(db, room_id, connection):
    # One statement, so membership/epoch/session are from the same DB snapshot.
    row = (await db.execute(select(Room, RoomMember, RoomDeliveryStream, Session)
        .outerjoin(RoomMember, (RoomMember.room_id == Room.id) & (RoomMember.user_id == connection.user_id))
        .outerjoin(RoomDeliveryStream, (RoomDeliveryStream.room_id == Room.id) & (RoomDeliveryStream.user_id == connection.user_id))
        .outerjoin(Session, Session.id == connection.session_id).where(Room.id == room_id)
        .execution_options(populate_existing=True))).first()
    if row is None:
        return 4404, 0, None
    room, member, stream, session = row
    code = access_code(room, member, session, connection.user_id)
    return code, stream.position if stream else 0, delivery_epoch(room, None if room.owner_user_id == connection.user_id else member, stream)


class DeliveryFeed(RoomFeed):
    async def load_access(self, connections):
        expected_users = {connection.session_id: connection.user_id for connection in connections}
        async with session_factory()() as db:
            rows = (await db.execute(select(Room, RoomMember, RoomDeliveryStream, Session).select_from(Room)
                .join(Session, Session.id.in_([c.session_id for c in connections]))
                .outerjoin(RoomMember, (RoomMember.room_id == Room.id) & (RoomMember.user_id == Session.user_id))
                .outerjoin(RoomDeliveryStream, (RoomDeliveryStream.room_id == Room.id) & (RoomDeliveryStream.user_id == Session.user_id))
                .where(Room.id == self.room_id))).all()
        return {session.id: (access_code(room, member, session, expected_users[session.id]),
            stream.position if stream else 0,
            delivery_epoch(room, None if room.owner_user_id == session.user_id else member, stream))
            for room, member, stream, session in rows}

    async def close_connection(self, connection, code, resync=False):
        self.connections.discard(connection)
        try:
            if resync:
                await asyncio.wait_for(connection.websocket.send_json({'type': 'resync_required'}), SEND_TIMEOUT_SECONDS)
            await asyncio.wait_for(connection.websocket.close(code=code), SEND_TIMEOUT_SECONDS)
        except (RuntimeError, OSError, WebSocketDisconnect, asyncio.TimeoutError):
            pass

    async def load_pending(self, connections):
        boundaries = {}
        for connection in connections:
            boundaries[connection.user_id] = min(boundaries.get(connection.user_id, connection.cursor), connection.cursor)
        requested = values(column('user_id', PG_UUID(as_uuid=True)), column('after', BigInteger)).data(
            list(boundaries.items())).alias('requested')
        pending = select(RoomDelivery).where(RoomDelivery.room_id == self.room_id,
            RoomDelivery.user_id == requested.c.user_id, RoomDelivery.position > requested.c.after
        ).order_by(RoomDelivery.position).limit(BATCH_SIZE).lateral('pending')
        async with session_factory()() as db:
            rows = (await db.execute(select(pending).select_from(requested.join(pending, true()))
                .order_by(pending.c.user_id, pending.c.position))).mappings().all()
        result = {user_id: [] for user_id in boundaries}
        for row in rows:
            result[row['user_id']].append(row)
        return result

    async def send_pending(self, connection, rows, access):
        if connection not in self.connections:
            return
        # Run even when there are no events: revocation/kick on an idle room must
        # close the socket. Recheck after loading pending rows, before enqueueing.
        code, head, epoch = access
        if rows and not code:
            async with session_factory()() as db:
                code, head, epoch = await current_scope(db, self.room_id, connection)
        if code:
            await self.close_connection(connection, code)
            return
        try:
            cursor_codec().decode(connection.token, self.room_id, connection.user_id, LIVE_SCOPE, 'room_live', epoch)
            if connection.cursor > head:
                raise HTTPException(status_code=409)
        except HTTPException:
            await self.close_connection(connection, 4409, resync=True)
            return
        page = [row for row in rows if connection.cursor < row['position'] <= head]
        if not page:
            return
        position = page[-1]['position']
        token = cursor_codec().encode(self.room_id, connection.user_id, LIVE_SCOPE, 'room_live', epoch,
            created=position, updated=0)
        try:
            await asyncio.wait_for(connection.websocket.send_json({'type': 'invalidation',
                'deliveries': [{'id': str(row['id']), 'scopes': row['scopes'], 'channel_ids': row['channel_ids']} for row in page],
                'cursor': token}), SEND_TIMEOUT_SECONDS)
            connection.cursor, connection.token = position, token
        except (RuntimeError, OSError, WebSocketDisconnect, asyncio.TimeoutError):
            await self.close_connection(connection, 1013)

    async def poll_once(self):
        connections = list(self.connections)
        if not connections:
            return 0
        rows = await self.load_pending(connections)
        access = await self.load_access(connections)
        await asyncio.gather(*(self.send_pending(connection, rows[connection.user_id],
            access.get(connection.session_id, (4401, 0, None))) for connection in connections))
        return EVENT_BATCH_SIZE if any(len(page) == BATCH_SIZE for page in rows.values()) else 0


class DeliveryHub(LiveHub):
    async def add(self, room_id, host_id, connection):
        async with self.lock:
            feed = self.feeds.get(room_id)
            if feed is None:
                feed = DeliveryFeed(room_id, host_id)
                self.feeds[room_id] = feed
            feed.connections.add(connection)
            if feed.task is None or feed.task.done():
                feed.task = asyncio.create_task(feed.run())
            url = os.getenv('REDIS_URL')
            if url and (self.listener is None or self.listener.done()):
                self.listener = asyncio.create_task(self.listen(url))


hub = DeliveryHub()


@router.websocket('/api/v2/rooms/{room_id}/live')
async def room_live(websocket: WebSocket, room_id: UUID):
    if not origin_allowed(websocket.headers.get('origin')):
        await websocket.close(code=4403)
        return
    if set(websocket.query_params) - {'cursor'}:
        await websocket.close(code=4400)
        return
    async with session_factory()() as db:
        identity = await lookup_session(db, websocket.cookies.get(COOKIE_NAME))
        if identity is None:
            await websocket.close(code=4401)
            return
        user, session = identity
        connection = DeliveryConnection(websocket, user.id, session.id, 0, '', b'')
        code, head, epoch = await current_scope(db, room_id, connection)
        if code:
            await websocket.close(code=code)
            return
        token = websocket.query_params.get('cursor')
        try:
            position = cursor_codec().decode(token, room_id, user.id, LIVE_SCOPE, 'room_live', epoch).created if token is not None else head
            if position > head:
                raise HTTPException(status_code=409)
        except HTTPException:
            await websocket.accept()
            await websocket.send_json({'type': 'resync_required'})
            await websocket.close(code=4409)
            return
        connection.cursor, connection.epoch = position, epoch
        connection.token = token if token is not None else cursor_codec().encode(room_id, user.id, LIVE_SCOPE,
            'room_live', epoch, created=position, updated=0)
        room = await db.get(Room, room_id)
        host_id = room.owner_user_id
    await websocket.accept()
    await websocket.send_json({'type': 'ready', 'cursor': connection.token})
    await hub.add(room_id, host_id, connection)
    try:
        while True:
            data = await websocket.receive()
            if data['type'] == 'websocket.disconnect':
                break
            await websocket.close(code=1003)
            break
    finally:
        await hub.remove(room_id, connection)
