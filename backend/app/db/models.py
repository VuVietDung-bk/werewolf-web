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
    chat_policy: Mapped[dict] = mapped_column(JSONB, nullable=False,
                                               server_default=text("'{}'::jsonb"))
    result_label: Mapped[str | None] = mapped_column(String(120))
    result_reason: Mapped[str | None] = mapped_column(String(2000))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    public_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
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
        CheckConstraint("publication_disposition IN ('published', 'pending', 'held', 'skipped')",
                        name="ck_player_states_publication_disposition"),
        ForeignKeyConstraint(["room_id", "user_id"],
                             ["role_assignments.room_id", "role_assignments.user_id"],
                             ondelete="CASCADE"),
    )

    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    is_alive: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    effects: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    public_is_alive: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    publication_disposition: Mapped[str] = mapped_column(String(16), nullable=False,
                                                         server_default="published")
    public_role_name: Mapped[str | None] = mapped_column(String(80))
    current_role_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    faction_label_override: Mapped[str | None] = mapped_column(String(80))
    team_chat_override_set: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    team_chat_label_override: Mapped[str | None] = mapped_column(String(80))


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
    current_text: Mapped[str | None] = mapped_column(String(2000))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrivateMessage(Base):
    __tablename__ = "private_messages"
    __table_args__ = (
        ForeignKeyConstraint(["room_id", "user_id"],
                             ["player_states.room_id", "player_states.user_id"],
                             ondelete="CASCADE"),
        Index("ix_private_messages_room_user_created", "room_id", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sender_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("game_events.id", ondelete="CASCADE"),
        unique=True, nullable=False
    )
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("channel IN ('common', 'team', 'host')", name="ck_chat_messages_channel"),
        Index("ix_chat_messages_room_channel_sequence", "room_id", "channel", "sequence"),
        Index("ix_chat_messages_room_team_sequence", "room_id", "team_label", "sequence"),
        Index("ix_chat_messages_room_participant_sequence", "room_id", "participant_user_id", "sequence"),
        Index("ix_chat_messages_team_recipients", "recipient_ids", postgresql_using="gin"),
        Index("ix_chat_messages_room_sender_created", "room_id", "sender_user_id", "created_at"),
        Index("ix_chat_messages_room_updated_sequence", "room_id", "updated_sequence"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("game_events.id", ondelete="CASCADE"),
        unique=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(12), nullable=False)
    team_label: Mapped[str | None] = mapped_column(String(80))
    participant_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    sender_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_ids: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                 server_default=text("'[]'::jsonb"))
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatMute(Base):
    __tablename__ = "chat_mutes"
    __table_args__ = (
        ForeignKeyConstraint(["room_id", "user_id"],
                             ["room_members.room_id", "room_members.user_id"], ondelete="CASCADE"),
    )

    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    is_muted: Mapped[bool] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                                ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now(), nullable=False)


class RoleRequest(Base):
    __tablename__ = "role_requests"
    __table_args__ = (
        ForeignKeyConstraint(["room_id", "user_id"],
                             ["player_states.room_id", "player_states.user_id"], ondelete="CASCADE"),
        Index("ix_role_requests_room_sequence", "room_id", "sequence"),
        Index("ix_role_requests_room_updated_sequence", "room_id", "updated_sequence"),
        Index("ix_role_requests_room_user_created", "room_id", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                           ForeignKey("game_events.id", ondelete="CASCADE"),
                                           unique=True, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    role_name: Mapped[str] = mapped_column(String(80), nullable=False)
    phase_type: Mapped[str] = mapped_column(String(16), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    reply_text: Mapped[str | None] = mapped_column(String(2000))
    linked_event_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="'[]'::jsonb")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now(), nullable=False)


class ContentRevision(Base):
    """Current presentation of editable content; the event journal keeps every version."""

    __tablename__ = "content_revisions"
    __table_args__ = (Index("ix_content_revisions_room", "room_id"),)

    source_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("game_events.id", ondelete="CASCADE"), primary_key=True
    )
    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    current_text: Mapped[str | None] = mapped_column(String(2000))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoteRound(Base):
    __tablename__ = "vote_rounds"
    __table_args__ = (
        CheckConstraint("day_number >= 0", name="ck_vote_rounds_day_number"),
        UniqueConstraint("decision_event_id", name="uq_vote_rounds_decision_event"),
        UniqueConstraint("publication_event_id", name="uq_vote_rounds_publication_event"),
        Index("uq_vote_rounds_one_open", "room_id", unique=True,
              postgresql_where=text("status = 'open'")),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    room_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_text: Mapped[str | None] = mapped_column(String(2000))
    decision_reason: Mapped[str | None] = mapped_column(String(1000))
    decision_target_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", name="fk_vote_rounds_decision_target",
                                         ondelete="RESTRICT")
    )
    decision_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("game_events.id", name="fk_vote_rounds_decision_event",
                                         ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("game_events.id", name="fk_vote_rounds_publication_event",
                                         ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoteBallot(Base):
    __tablename__ = "vote_ballots"
    __table_args__ = (
        UniqueConstraint("vote_round_id", "voter_user_id", name="uq_vote_ballots_voter"),
        CheckConstraint("choice IN ('target', 'skip', 'withdrawn')", name="ck_vote_ballots_choice"),
        CheckConstraint("(choice = 'target') = (target_user_id IS NOT NULL)",
                        name="ck_vote_ballots_target"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    vote_round_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("vote_rounds.id", ondelete="CASCADE"), nullable=False
    )
    voter_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    choice: Mapped[str] = mapped_column(String(16), nullable=False)
    target_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
