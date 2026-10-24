"""Outbox retry and database replay without a publisher or Redis hint."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import redis.asyncio as redis
from sqlalchemy import select

from app import live
from app.auth import COOKIE_NAME, lookup_session, new_session
from app.db.events import append_event
from app.db.models import GameEvent, Outbox, Room, User
from app.db.session import session_factory
from app.live import Connection, RoomFeed
from app.main import app
from app.outbox_worker import CHANNEL, publish_batch

ORIGIN = "http://localhost:5173"


class Publisher:
    def __init__(self) -> None:
        self.fail = True
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        if self.fail:
            raise redis.ConnectionError("Redis stopped")
        self.messages.append((channel, message))
        return 0  # No subscriber is also a successful Redis publish.


class Socket:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.ready = asyncio.Event()

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)
        self.ready.set()


def test_outbox_retries_and_websocket_reconciles_without_worker(monkeypatch) -> None:
    async def run() -> None:
        room_id = user_id = None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as host:
            try:
                user_id = (await host.post("/api/auth/guest", json={"display_name": "Outbox host"},
                                           headers={"Origin": ORIGIN})).json()["id"]
                room = (await host.post("/api/rooms", json={"name": "Outbox recovery", "seat_limit": 2},
                                        headers={"Origin": ORIGIN})).json()
                room_id = room["id"]
                first_sequence = room["version"]
                changed = await host.patch(f"/api/rooms/{room_id}/settings", json={
                    "name": "Outbox recovery updated", "seat_limit": 2,
                    "registration_open": True}, headers={"Origin": ORIGIN})
                assert changed.status_code == 200
                expected_sequence = changed.json()["version"]
                async with session_factory()() as db:
                    identity = await lookup_session(db, host.cookies.get(COOKIE_NAME))
                    assert identity is not None
                    session_id = identity[1].id
                    pending = (await db.scalars(select(Outbox).join(
                        GameEvent, GameEvent.id == Outbox.event_id).where(
                        GameEvent.room_id == UUID(room_id)).order_by(GameEvent.sequence))).all()
                    assert len(pending) == 2 and all(row.published_at is None for row in pending)

                # While the worker and Redis are absent, the feed must still read DB.
                socket = Socket()
                feed = RoomFeed(UUID(room_id), UUID(user_id))
                connection = Connection(websocket=socket, user_id=UUID(user_id),
                                        session_id=session_id, cursor=first_sequence)
                feed.connections.add(connection)
                assert await feed.poll_once() == 1
                assert socket.messages == [{
                    "type": "game.event", "event_id": str(pending[1].event_id),
                    "sequence": expected_sequence, "kind": "room.settings_updated",
                }]
                assert await feed.poll_once() == 0

                publisher = Publisher()
                async with session_factory()() as db:
                    assert await publish_batch(db, publisher, UUID(room_id)) == (0, True)
                async with session_factory()() as db:
                    retriable = (await db.scalars(select(Outbox).join(
                        GameEvent, GameEvent.id == Outbox.event_id).where(
                        GameEvent.room_id == UUID(room_id)).order_by(GameEvent.sequence))).all()
                    assert [item.attempts for item in retriable] == [1, 0]
                    assert all(item.published_at is None for item in retriable)

                publisher.fail = False
                async with session_factory()() as db:
                    assert await publish_batch(db, publisher, UUID(room_id)) == (2, False)
                assert [channel for channel, _ in publisher.messages] == [CHANNEL, CHANNEL]
                assert [json.loads(value) for _, value in publisher.messages] == [
                    {"room_id": room_id, "sequence": first_sequence},
                    {"room_id": room_id, "sequence": expected_sequence},
                ]
                async with session_factory()() as db:
                    assert await publish_batch(db, publisher, UUID(room_id)) == (0, False)
                    published = (await db.scalars(select(Outbox).join(
                        GameEvent, GameEvent.id == Outbox.event_id).where(
                        GameEvent.room_id == UUID(room_id)).order_by(GameEvent.sequence))).all()
                    assert [item.attempts for item in published] == [2, 1]
                    assert all(item.published_at is not None for item in published)
                assert await feed.poll_once() == 0  # Redis hint cannot duplicate a DB event.

                # A live feed keeps polling while Redis is configured but unreachable.
                monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
                socket.ready.clear()
                task = asyncio.create_task(feed.run())
                try:
                    await asyncio.sleep(0.05)
                    newer = await host.patch(f"/api/rooms/{room_id}/settings", json={
                        "name": "Recovered without Redis", "seat_limit": 2,
                        "registration_open": True}, headers={"Origin": ORIGIN})
                    assert newer.status_code == 200
                    await asyncio.wait_for(socket.ready.wait(), timeout=2)
                    assert [item["sequence"] for item in socket.messages] == [
                        expected_sequence, newer.json()["version"]]
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                # Competing workers skip each other's locked outbox rows.
                for name in ("Concurrent A", "Concurrent B"):
                    assert (await host.patch(f"/api/rooms/{room_id}/settings", json={
                        "name": name, "seat_limit": 2, "registration_open": True},
                        headers={"Origin": ORIGIN})).status_code == 200

                class SlowPublisher(Publisher):
                    async def publish(self, channel: str, message: str) -> int:
                        await asyncio.sleep(0.05)
                        return await super().publish(channel, message)

                slow = SlowPublisher()
                slow.fail = False

                async def drain() -> tuple[int, bool]:
                    async with session_factory()() as db:
                        return await publish_batch(db, slow, UUID(room_id))

                results = await asyncio.gather(drain(), drain())
                assert sorted(result[0] for result in results) == [0, 3]
                assert len({json.loads(value)["sequence"] for _, value in slow.messages}) == 3
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())


def test_reconnect_during_feed_query_does_not_skip_older_events(monkeypatch) -> None:
    async def run() -> None:
        async with session_factory()() as db:
            user = User(display_name="Cursor race host")
            db.add(user)
            await db.flush()
            session, _ = new_session(user.id)
            db.add(session)
            room = Room(owner_user_id=user.id, code=uuid4().hex[:12],
                        name="Cursor race", seat_limit=2)
            db.add(room)
            await db.flush()
            for index in range(1, 4):
                await append_event(db, room.id, user.id, f"room.test_{index}", "room", {})
            await db.commit()
            room_id, user_id, session_id = room.id, user.id, session.id

        feed = RoomFeed(room_id, user_id)
        old_socket, new_socket = Socket(), Socket()
        feed.connections.add(Connection(old_socket, user_id, session_id, 2))
        entered, proceed = asyncio.Event(), asyncio.Event()
        real_factory = live.session_factory
        paused = True

        class PausedSession:
            async def __aenter__(self):
                entered.set()
                await proceed.wait()
                self.manager = real_factory()()
                return await self.manager.__aenter__()

            async def __aexit__(self, *args):
                return await self.manager.__aexit__(*args)

        def intercept_factory():
            nonlocal paused
            if paused:
                paused = False
                return lambda: PausedSession()
            return real_factory()

        monkeypatch.setattr(live, "session_factory", intercept_factory)
        try:
            polling = asyncio.create_task(feed.poll_once())
            await asyncio.wait_for(entered.wait(), timeout=1)
            feed.connections.add(Connection(new_socket, user_id, session_id, 0))
            proceed.set()
            assert await polling == 1
            assert [item["sequence"] for item in old_socket.messages] == [3]
            assert new_socket.messages == []
            assert await feed.poll_once() == 3
            assert [item["sequence"] for item in new_socket.messages] == [1, 2, 3]
            assert [item["sequence"] for item in old_socket.messages] == [3]
        finally:
            proceed.set()
            monkeypatch.setattr(live, "session_factory", real_factory)
            async with session_factory()() as db:
                await db.delete(await db.get(Room, room_id))
                await db.commit()
                await db.delete(await db.get(User, user_id))
                await db.commit()

    asyncio.run(run())
