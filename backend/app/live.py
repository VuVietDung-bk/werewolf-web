"""Room WebSocket notifications backed by committed database events.

One polling task serves all sockets for a room in this API process. Redis hints
wake the task quickly when available; timed PostgreSQL reads recover any hint
lost to worker, Redis, network or process outages.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
import redis.asyncio as redis
from sqlalchemy import select

from app.auth import (COOKIE_NAME, POSTGAME_POLICY_VERSION, lookup_session,
                      now_utc, origin_allowed, require_room_access)
from app.db.models import (ChatMessage, GameEvent, PrivateMessage, Room,
                           RoomMember, Session)
from app.db.session import session_factory
from app.outbox_worker import CHANNEL, redis_client

router = APIRouter(tags=["live"])
logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 0.4
EVENT_BATCH_SIZE = 100
SELF_VISIBLE_PLAYER_ACTIONS = {
    "player.kill", "player.revive", "player.add_effect", "player.remove_effect",
    "player.change_role", "player.change_faction", "player.change_team",
}


@dataclass(eq=False)
class Connection:
    websocket: WebSocket
    user_id: UUID
    session_id: UUID
    cursor: int


class RoomFeed:
    def __init__(self, room_id: UUID, host_id: UUID):
        self.room_id = room_id
        self.host_id = host_id
        self.connections: set[Connection] = set()
        self.task: asyncio.Task | None = None
        self.wake = asyncio.Event()

    async def run(self) -> None:
        while self.connections:
            try:
                count = await self.poll_once()
                if count < EVENT_BATCH_SIZE:
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout=POLL_INTERVAL_SECONDS)
                    except asyncio.TimeoutError:
                        pass
                    self.wake.clear()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Room feed failed for room %s", self.room_id)
                await asyncio.sleep(1)

    async def poll_once(self) -> int:
        if not self.connections:
            return 0
        # Snapshot recipients before the first await. A reconnect that joins
        # during the DB query must get its own replay from its older cursor.
        connections = list(self.connections)
        cursor = min(connection.cursor for connection in connections)
        async with session_factory()() as db:
            events = (await db.scalars(select(GameEvent).where(
                GameEvent.room_id == self.room_id, GameEvent.sequence > cursor
            ).order_by(GameEvent.sequence).limit(EVENT_BATCH_SIZE))).all()
            if not events:
                return 0
            session_ids = [connection.session_id for connection in connections]
            valid_sessions = set((await db.scalars(select(Session.id).where(
                Session.id.in_(session_ids), Session.revoked_at.is_(None),
                Session.expires_at > now_utc()))).all())
            active_members = set((await db.scalars(select(RoomMember.user_id).where(
                RoomMember.room_id == self.room_id, RoomMember.left_at.is_(None),
                RoomMember.consent_version == POSTGAME_POLICY_VERSION))).all())
            chat_events = [event.id for event in events if event.kind == "chat.sent"]
            private_events = [event.id for event in events if event.kind == "player.private_message_sent"]
            chats = {message.event_id: message for message in (await db.scalars(
                select(ChatMessage).where(ChatMessage.event_id.in_(chat_events)))).all()} if chat_events else {}
            privates = {message.event_id: message.user_id for message in (await db.scalars(
                select(PrivateMessage).where(PrivateMessage.event_id.in_(private_events)))).all()} if private_events else {}

        for connection in connections:
            if connection not in self.connections:
                continue
            if (connection.session_id not in valid_sessions or
                    (connection.user_id != self.host_id and connection.user_id not in active_members)):
                self.connections.discard(connection)
                try:
                    await connection.websocket.close(code=4403)
                except (RuntimeError, OSError):
                    pass
        for event in events:
            for connection in connections:
                if connection not in self.connections:
                    continue
                if event.sequence <= connection.cursor:
                    continue
                if not may_receive(event, connection.user_id, self.host_id,
                                   chats.get(event.id), privates.get(event.id)):
                    connection.cursor = event.sequence
                    continue
                try:
                    await connection.websocket.send_json({
                        "type": "game.event", "event_id": str(event.id),
                        "sequence": event.sequence, "kind": event.kind,
                    })
                    connection.cursor = event.sequence
                except (RuntimeError, OSError, WebSocketDisconnect):
                    self.connections.discard(connection)
        return len(events)


def may_receive(event: GameEvent, user_id: UUID, host_id: UUID,
                chat: ChatMessage | None, private_recipient: UUID | None) -> bool:
    if user_id == host_id:
        return True
    if event.kind == "chat.sent":
        if chat is None:
            return False
        if chat.channel == "common":
            return True
        if chat.channel == "team":
            return str(user_id) in chat.recipient_ids
        return chat.participant_user_id == user_id
    if event.kind == "chat.revised":
        if event.payload.get("channel") == "common":
            return True
        if event.payload.get("channel") == "team":
            return str(user_id) in event.payload.get("recipient_ids", [])
        return event.payload.get("participant_user_id") == str(user_id)
    if event.kind == "player.private_message_sent":
        return private_recipient == user_id
    if event.kind == "chat.mute_changed":
        return event.payload.get("request", {}).get("user_id") == str(user_id)
    if event.kind in {"role.request_submitted", "role.request_reviewed", "role.request_withdrawn"}:
        return event.payload.get("request", {}).get("user_id") == str(user_id)
    if event.kind in SELF_VISIBLE_PLAYER_ACTIONS:
        return event.payload.get("request", {}).get("user_id") == str(user_id)
    return event.visibility == "room"


class LiveHub:
    def __init__(self):
        self.feeds: dict[UUID, RoomFeed] = {}
        self.lock = asyncio.Lock()
        self.listener: asyncio.Task | None = None

    async def listen(self, url: str) -> None:
        """Wake room feeds on Redis hints; reconnect after Redis restarts."""
        while True:
            client = redis_client(url)
            try:
                async with client.pubsub() as pubsub:
                    await pubsub.subscribe(CHANNEL)
                    while True:
                        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                        if message is None:
                            continue
                        try:
                            data = json.loads(message["data"])
                            room_id = UUID(data["room_id"])
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            continue
                        feed = self.feeds.get(room_id)
                        if feed is not None:
                            feed.wake.set()
            except asyncio.CancelledError:
                raise
            except (redis.RedisError, OSError, asyncio.TimeoutError):
                logger.warning("Redis subscriber unavailable; room feeds continue polling PostgreSQL")
                await asyncio.sleep(1)
            finally:
                await client.aclose()

    async def add(self, room_id: UUID, host_id: UUID, connection: Connection) -> None:
        async with self.lock:
            feed = self.feeds.get(room_id)
            if feed is None:
                feed = RoomFeed(room_id, host_id)
                self.feeds[room_id] = feed
            feed.connections.add(connection)
            if feed.task is None or feed.task.done():
                feed.task = asyncio.create_task(feed.run())
            url = os.getenv("REDIS_URL")
            if url and (self.listener is None or self.listener.done()):
                self.listener = asyncio.create_task(self.listen(url))

    async def remove(self, room_id: UUID, connection: Connection) -> None:
        async with self.lock:
            feed = self.feeds.get(room_id)
            if feed is None:
                return
            feed.connections.discard(connection)
            if not feed.connections:
                self.feeds.pop(room_id, None)
                if feed.task is not None:
                    feed.task.cancel()
                    try:
                        await feed.task
                    except asyncio.CancelledError:
                        pass
            if not self.feeds and self.listener is not None:
                self.listener.cancel()
                try:
                    await self.listener
                except asyncio.CancelledError:
                    pass
                self.listener = None


hub = LiveHub()


@router.websocket("/api/rooms/{room_id}/live")
async def room_live(websocket: WebSocket, room_id: UUID) -> None:
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    async with session_factory()() as db:
        identity = await lookup_session(db, websocket.cookies.get(COOKIE_NAME))
        if identity is None:
            await websocket.close(code=4401)
            return
        user, session = identity
        try:
            await require_room_access(db, room_id, user.id)
        except HTTPException as error:
            await websocket.close(code=4404 if error.status_code == 404 else 4403)
            return
        room = await db.get(Room, room_id)
        raw_cursor = websocket.query_params.get("after_sequence")
        if raw_cursor is not None and (not raw_cursor.isascii() or not raw_cursor.isdecimal()
                                       or len(raw_cursor) > 20):
            await websocket.close(code=4400)
            return
        cursor = min(int(raw_cursor), room.version) if raw_cursor is not None else room.version
        connection = Connection(websocket=websocket, user_id=user.id,
                                session_id=session.id, cursor=cursor)
        host_id = room.owner_user_id
    await websocket.accept()
    await websocket.send_json({"type": "ready", "cursor": cursor})
    await hub.add(room_id, host_id, connection)
    try:
        while True:
            data = await websocket.receive()
            if data["type"] == "websocket.disconnect":
                break
            # Notifications are server-to-client only; do not accept client commands here.
            await websocket.close(code=1003)
            break
    finally:
        await hub.remove(room_id, connection)
