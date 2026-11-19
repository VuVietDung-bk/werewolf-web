"""Publish queued notices in the same transaction as a phase transition."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.events import append_event
from app.db.models import GameEvent, PlayerState, Room, RoomMember, ScheduledAnnouncement


async def cancel_scheduled(db: AsyncSession, item: ScheduledAnnouncement,
                           actor_user_id: UUID, reason: str,
                           client_request_id: UUID | None = None) -> GameEvent:
    if item.kind == "status" and item.player_id is not None:
        state = await db.get(PlayerState, (item.room_id, item.player_id))
        if state is not None and state.publication_disposition == "scheduled":
            state.publication_disposition = ("pending" if state.is_alive != state.public_is_alive
                                             else "published")
    event = await append_event(db, item.room_id, actor_user_id,
                               "event.scheduled_announcement_cancelled", "host",
                               {"scheduled_event_id": str(item.id), "kind": item.kind,
                                "player_id": str(item.player_id) if item.player_id else None,
                                "reason": reason}, client_request_id=client_request_id)
    await db.delete(item)
    await db.flush()
    return event


async def cancel_status_schedule(db: AsyncSession, room_id: UUID, player_id: UUID,
                                 actor_user_id: UUID, reason: str) -> None:
    item = await db.scalar(select(ScheduledAnnouncement).where(
        ScheduledAnnouncement.room_id == room_id,
        ScheduledAnnouncement.player_id == player_id))
    if item is not None:
        await cancel_scheduled(db, item, actor_user_id, reason)


async def release_scheduled(db: AsyncSession, room: Room, actor_user_id: UUID) -> None:
    items = (await db.scalars(select(ScheduledAnnouncement).where(
        ScheduledAnnouncement.room_id == room.id
    ).order_by(ScheduledAnnouncement.created_at, ScheduledAnnouncement.id))).all()
    for item in items:
        payload = item.payload
        if item.kind == "status":
            state = await db.get(PlayerState, (room.id, item.player_id))
            member = await db.get(RoomMember, (room.id, item.player_id))
            if (state is None or member is None or member.left_at is not None
                    or state.publication_disposition != "scheduled"
                    or state.is_alive != payload["is_alive"]
                    or state.public_is_alive == state.is_alive):
                await cancel_scheduled(db, item, actor_user_id, "Trạng thái không còn phù hợp khi chuyển pha")
                continue
            state.public_is_alive = state.is_alive
            state.publication_disposition = "published"
            if payload["revealed_role_name"] is not None:
                state.public_role_name = payload["revealed_role_name"]
            public_kind = "player.revival_announced" if state.is_alive else "player.death_announced"
            public_payload = {"user_id": str(item.player_id),
                              "display_name": payload["display_name"],
                              "seat_number": payload["seat_number"],
                              "status": "alive" if state.is_alive else "dead",
                              "public_cause": payload["public_cause"],
                              "revealed_role_name": payload["revealed_role_name"],
                              "phase_type": room.phase_type, "round_number": room.round_number}
        else:
            public_kind = "event.custom_announcement"
            public_payload = {"text": payload["text"], "kind": payload["kind"],
                              "related_player": payload["related_player"],
                              "source_player": payload["source_player"],
                              "phase_type": room.phase_type, "round_number": room.round_number}
        public_event = await append_event(db, room.id, actor_user_id, public_kind, "room", public_payload)
        await append_event(db, room.id, actor_user_id, "event.scheduled_announcement_released", "host",
                           {"scheduled_event_id": str(item.id), "public_event_id": str(public_event.id),
                            "kind": item.kind, "phase_type": room.phase_type,
                            "round_number": room.round_number})
        await db.delete(item)
    await db.flush()


async def cancel_all_scheduled(db: AsyncSession, room_id: UUID, actor_user_id: UUID,
                               reason: str) -> None:
    items = (await db.scalars(select(ScheduledAnnouncement).where(
        ScheduledAnnouncement.room_id == room_id))).all()
    for item in items:
        await cancel_scheduled(db, item, actor_user_id, reason)
