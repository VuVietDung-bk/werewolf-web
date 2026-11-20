"""Persist channel identity in the same transaction as its source writes; never in GET."""
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RoomChannel, RoomMember, RoleDefinition, RoleSnapshot


async def register_channels(db: AsyncSession, room_id: UUID, entries: set[tuple[str, str]]) -> None:
    if not entries:
        return
    rows = [{"id": uuid4(), "room_id": room_id, "kind": kind, "channel_key": key}
            for kind, key in sorted(entries)]
    await db.execute(insert(RoomChannel).values(rows).on_conflict_do_nothing(
        constraint="uq_room_channels_identity"))


async def register_for_event(db: AsyncSession, room_id: UUID, kind: str, payload: dict) -> None:
    entries: set[tuple[str, str]] = set()
    if kind in {"room.created", "game.started", "role_set.sample_added"}:
        entries.update((name, "") for name in ("common", "announcement", "vote"))
        entries.update(("moderator_dm", str(user_id)) for user_id in (await db.scalars(
            select(RoomMember.user_id).where(RoomMember.room_id == room_id))).all())
        labels = (await db.scalars(select(RoleDefinition.team_chat_label).where(
            RoleDefinition.room_id == room_id))).all()
        entries.update(("team", label) for label in labels if label)
        snapshot = await db.get(RoleSnapshot, room_id)
        if snapshot:
            entries.update(("team", role["team_chat_label"]) for role in snapshot.definitions if role.get("team_chat_label"))
    elif kind == "member.joined":
        entries.add(("moderator_dm", payload["user_id"]))
    elif kind in {"role.created", "role.updated"} and payload.get("team_chat_label"):
        entries.add(("team", payload["team_chat_label"]))
    elif kind in {"player.change_team", "player.change_role"} and payload.get("current", {}).get("team_chat_label"):
        entries.add(("team", payload["current"]["team_chat_label"]))
    elif kind == "chat.sent" and payload.get("team_label"):
        entries.add(("team", payload["team_label"]))
    await register_channels(db, room_id, entries)
