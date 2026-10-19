"""Persistent identities, room membership, and the ordered event journal."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    BigInteger,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint("seat_limit BETWEEN 2 AND 30", name="ck_rooms_seat_limit"),
        CheckConstraint("round_number >= 0", name="ck_rooms_round_number"),
        CheckConstraint("version >= 0", name="ck_rooms_version"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    seat_limit: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="open")
    phase_type: Mapped[str | None] = mapped_column(String(16))
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_paused: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    result_label: Mapped[str | None] = mapped_column(String(120))
    result_reason: Mapped[str | None] = mapped_column(String(2000))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (
        Index("uq_room_members_active_seat", "room_id", "seat_number", unique=True,
              postgresql_where=text("left_at IS NULL")),
        CheckConstraint("seat_number BETWEEN 1 AND 30", name="ck_room_members_seat_number"),
        CheckConstraint("left_at IS NOT NULL OR seat_number IS NOT NULL",
                        name="ck_room_members_active_has_seat"),
        Index("ix_room_members_user_id", "user_id"),
    )

    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    seat_number: Mapped[int | None] = mapped_column(SmallInteger)
    consent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GameEvent(Base):
    __tablename__ = "game_events"
    __table_args__ = (
        UniqueConstraint("room_id", "sequence", name="uq_game_events_room_sequence"),
        UniqueConstraint("room_id", "client_request_id", name="uq_game_events_request"),
        Index("ix_game_events_room_created", "room_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    client_request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("game_events.id", ondelete="CASCADE"),
        unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)


class RoleSet(Base):
    __tablename__ = "role_sets"

    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="Bộ role của phòng")


class RoleIcon(Base):
    __tablename__ = "role_icons"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    image_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    width: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    height: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RoleDefinition(Base):
    __tablename__ = "role_definitions"
    __table_args__ = (
        UniqueConstraint("room_id", "name", name="uq_role_definitions_room_name"),
        CheckConstraint("quantity BETWEEN 0 AND 30", name="ck_role_definitions_quantity"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("role_sets.room_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(4000), nullable=False)
    faction_label: Mapped[str] = mapped_column(String(80), nullable=False)
    seer_result: Mapped[str] = mapped_column(String(80), nullable=False,
                                            server_default="Chưa thiết lập")
    team_chat_label: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    role_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    request_phases: Mapped[list] = mapped_column(JSONB, nullable=False)
    request_when_dead: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    host_contact: Mapped[dict] = mapped_column(JSONB, nullable=False)
    icon_key: Mapped[str] = mapped_column(String(24), nullable=False, server_default="auto")
    icon_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("role_icons.id", ondelete="SET NULL")
    )


class RoleSnapshot(Base):
    __tablename__ = "role_snapshots"

    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    set_name: Mapped[str] = mapped_column(String(120), nullable=False)
    definitions: Mapped[list] = mapped_column(JSONB, nullable=False)
    distribution_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        ForeignKeyConstraint(["room_id", "user_id"], ["room_members.room_id", "room_members.user_id"],
                             ondelete="CASCADE"),
        Index("ix_role_assignments_room_role", "room_id", "role_id"),
    )

    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("role_snapshots.room_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)


class PlayerState(Base):
    __tablename__ = "player_states"
    __table_args__ = (
        ForeignKeyConstraint(["room_id", "user_id"],
                             ["role_assignments.room_id", "role_assignments.user_id"],
                             ondelete="CASCADE"),
    )

    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    is_alive: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    effects: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


class PlayerNote(Base):
    __tablename__ = "player_notes"
    __table_args__ = (
        ForeignKeyConstraint(["room_id", "user_id"],
                             ["player_states.room_id", "player_states.user_id"],
                             ondelete="CASCADE"),
        Index("ix_player_notes_room_user_created", "room_id", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DraftTurn(Base):
    __tablename__ = "draft_turns"
    __table_args__ = (
        UniqueConstraint("room_id", "user_id", name="uq_draft_turns_room_user"),
        ForeignKeyConstraint(["room_id", "user_id"], ["room_members.room_id", "room_members.user_id"],
                             ondelete="CASCADE"),
        CheckConstraint("turn_index >= 1", name="ck_draft_turns_index"),
    )

    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("role_snapshots.room_id", ondelete="CASCADE"), primary_key=True
    )
    turn_index: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    offers: Mapped[list | None] = mapped_column(JSONB)
    chosen_role_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    chosen_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    choice_mode: Mapped[str | None] = mapped_column(String(24))
    choice_reason: Mapped[str | None] = mapped_column(String(1000))
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chosen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
