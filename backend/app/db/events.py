"""Append an ordered room event and an outbox row in the caller's transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GameEvent, Outbox, Room


async def append_event(
    db: AsyncSession,
    room_id: UUID,
    actor_user_id: UUID | None,
    kind: str,
    visibility: str,
    payload: dict,
    client_request_id: UUID | None = None,
) -> GameEvent:
    room = (await db.execute(select(Room).where(Room.id == room_id).with_for_update())).scalar_one()
    room.version += 1
    event = GameEvent(
        room_id=room_id,
        sequence=room.version,
        actor_user_id=actor_user_id,
        kind=kind,
        visibility=visibility,
        payload=payload,
        client_request_id=client_request_id,
    )
    db.add(event)
    await db.flush()
    db.add(Outbox(event_id=event.id))
    await db.flush()
    return event
