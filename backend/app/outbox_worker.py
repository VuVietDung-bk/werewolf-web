"""Publish committed outbox rows as content-free Redis wake-up hints.

The PostgreSQL event journal is authoritative. Redis Pub/Sub can lose hints, so
subscribers always reconcile from the journal by room sequence.
"""

import asyncio
import json
import logging
import os
import time
from uuid import UUID

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import now_utc
from app.db.models import GameEvent, Outbox
from app.db.session import session_factory

logger = logging.getLogger(__name__)
CHANNEL = "ma-soi:room-events"
HEARTBEAT_KEY = "ma-soi:outbox-worker-heartbeat"
BATCH_SIZE = 100


def redis_client(url: str) -> redis.Redis:
    return redis.from_url(url, decode_responses=True,
                          socket_connect_timeout=1, socket_timeout=1)


async def publish_batch(db: AsyncSession, client: redis.Redis,
                        room_id: UUID | None = None) -> tuple[int, bool]:
    """Claim unpublished rows; acknowledge only after PUBLISH succeeds.

    A crash after PUBLISH and before commit may publish a duplicate hint. The
    WebSocket feed reads unique event sequences from PostgreSQL, so that is safe.
    """
    async with db.begin():
        query = select(Outbox, GameEvent).join(
            GameEvent, GameEvent.id == Outbox.event_id).where(
            Outbox.published_at.is_(None))
        if room_id is not None:
            query = query.where(GameEvent.room_id == room_id)
        rows = (await db.execute(query.order_by(
            Outbox.created_at, Outbox.id).limit(BATCH_SIZE).with_for_update(
            of=Outbox, skip_locked=True))).all()
        published = 0
        failed = False
        for pending, event in rows:
            pending.attempts += 1
            try:
                await client.publish(CHANNEL, json.dumps({
                    "room_id": str(event.room_id), "sequence": event.sequence,
                }, separators=(",", ":")))
            except (redis.RedisError, OSError, asyncio.TimeoutError):
                failed = True
                logger.warning("Redis unavailable; outbox row %s will retry", pending.id)
                break
            pending.published_at = now_utc()
            published += 1
    return published, failed


async def run_worker(url: str, stop: asyncio.Event | None = None) -> None:
    client = redis_client(url)
    failures = 0
    last_heartbeat = 0.0
    try:
        while stop is None or not stop.is_set():
            try:
                async with session_factory()() as db:
                    published, failed = await publish_batch(db, client)
                if not failed and time.monotonic() - last_heartbeat >= 5:
                    await client.set(HEARTBEAT_KEY, str(time.time()), ex=20)
                    last_heartbeat = time.monotonic()
                failures = failures + 1 if failed else 0
                delay = min(10.0, 2.0 ** (failures - 1)) if failed else 0 if published == BATCH_SIZE else 0.25
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outbox batch failed; retrying")
                failures += 1
                delay = min(10.0, 2.0 ** (failures - 1))
            if delay:
                try:
                    if stop is not None:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    else:
                        await asyncio.sleep(delay)
                except asyncio.TimeoutError:
                    pass
    finally:
        await client.aclose()


def main() -> None:
    url = os.getenv("REDIS_URL")
    if not url:
        raise SystemExit("REDIS_URL is required for the outbox worker")
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker(url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
