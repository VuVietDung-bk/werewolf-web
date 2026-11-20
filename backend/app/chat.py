"""Persistent room chat; every read and send is authorized from current DB state."""

from datetime import datetime, timedelta
from math import ceil
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import String, and_, cast, func, literal, null, or_, select, true, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, now_utc, require_room_access, require_room_host
from app.db.events import append_event
from app.db.models import (ChatMessage, ChatMute, ContentRevision, GameEvent, PlayerState, PrivateMessage,
                           RoleAssignment, RoleRequest, RoleSnapshot, Room, RoomMember, User)
from app.db.session import get_db
from app.roles import effective_role_data

router = APIRouter(prefix="/api/rooms/{room_id}/chat", tags=["chat"])
Channel = Literal["common", "team", "host"]
DEFAULT_CHAT_POLICY = {
    "common_send_at_night": False,
    "team_send_in_day": False,
    "dead_common_send": False,
    "dead_team_send": False,
    "slow_mode_seconds": 0,
}
CHAT_BURST_LIMIT = 6
CHAT_BURST_SECONDS = 10
CHAT_MINUTE_LIMIT = 30
CHAT_MINUTE_SECONDS = 60


class ChatPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    common_send_at_night: bool
    team_send_in_day: bool
    dead_common_send: bool
    dead_team_send: bool
    slow_mode_seconds: int = Field(default=0, ge=0, le=10)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def valid_reason(self):
        self.reason = (self.reason or "").strip() or None
        if self.slow_mode_seconds == 1:
            raise ValueError("Slow mode phải tắt hoặc từ 2 đến 10 giây")
        return self


class ChatSendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    channel: Channel
    text: str = Field(min_length=1, max_length=2000)
    team_label: str | None = Field(default=None, max_length=80)
    participant_user_id: UUID | None = None

    @model_validator(mode="after")
    def normalize(self):
        self.text = self.text.strip()
        self.team_label = self.team_label.strip() if self.team_label else None
        if not self.text:
            raise ValueError("Cần nội dung tin nhắn")
        if (self.channel != "team" and self.team_label is not None) or (self.channel != "host" and self.participant_user_id is not None):
            raise ValueError("Tham số kênh chat không hợp lệ")
        return self


class ChatMessageOutput(BaseModel):
    source_kind: Literal["chat", "private_message", "role_request"] = "chat"
    id: UUID
    event_id: UUID
    sequence: int
    updated_sequence: int
    channel: Channel
    team_label: str | None
    participant_user_id: UUID | None
    sender_user_id: UUID
    sender_name: str
    text: str
    created_at: datetime
    edited_at: datetime | None = None
    phase_type: str | None = None
    round_number: int | None = None
    deleted: bool = False
    history: list[dict] | None = None


class ChatPage(BaseModel):
    messages: list[ChatMessageOutput]
    next_before_sequence: int | None
    next_after_sequence: int | None = None
    next_after_updated_sequence: int | None = None
    room_cursor: int


class ChatActivity(BaseModel):
    channel: Channel
    team_label: str | None
    participant_user_id: UUID | None
    sequence: int


class ChatParticipant(BaseModel):
    user_id: UUID
    display_name: str
    departed: bool


class ChatContext(BaseModel):
    team_labels: list[str]
    writable_team_labels: list[str]
    current_team_label: str | None
    policy: dict[str, bool | int]
    next_chat_at: datetime | None
    can_send_common: bool
    can_send_team: bool
    can_send_host: bool
    is_muted: bool
    mute_reason: str | None
    muted_players: list[dict[str, str]]
    activity: list[ChatActivity]
    dm_participants: list[ChatParticipant]


class ChatSendOutput(BaseModel):
    event_id: UUID
    repeated: bool


class ChatMuteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    muted: bool
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_reason(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("Cần lý do chặn hoặc mở chat")
        return self


@router.patch("/players/{player_id}/mute", response_model=ChatSendOutput)
async def change_chat_mute(room_id: UUID, player_id: UUID, body: ChatMuteInput,
                           user: User = Depends(current_user),
                           db: AsyncSession = Depends(get_db)) -> ChatSendOutput:
    await require_room_host(db, room_id, user.id)
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    request = {**body.model_dump(mode="json", exclude={"client_request_id"}),
               "user_id": str(player_id)}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "chat.mute_changed" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ChatSendOutput(event_id=prior.id, repeated=True)
    if room.status == "finished":
        raise HTTPException(status_code=409, detail="Trận đã kết thúc")
    member = await db.get(RoomMember, (room_id, player_id))
    if member is None or member.left_at is not None:
        raise HTTPException(status_code=404, detail="Người chơi không còn trong phòng")
    mute = await db.get(ChatMute, (room_id, player_id))
    if bool(mute and mute.is_muted) == body.muted:
        raise HTTPException(status_code=409, detail="Trạng thái chặn chat chưa thay đổi")
    previous = {"muted": mute.is_muted, "reason": mute.reason} if mute else {"muted": False, "reason": None}
    if mute is None:
        mute = ChatMute(room_id=room_id, user_id=player_id, is_muted=body.muted,
                        reason=body.reason, actor_user_id=user.id)
        db.add(mute)
    else:
        mute.is_muted = body.muted
        mute.reason = body.reason
        mute.actor_user_id = user.id
        mute.updated_at = now_utc()
    event = await append_event(db, room_id, user.id, "chat.mute_changed", "host",
                               {"request": request, "previous": previous,
                                "current": {"muted": body.muted, "reason": body.reason}},
                               client_request_id=body.client_request_id)
    await db.commit()
    return ChatSendOutput(event_id=event.id, repeated=False)


async def check_chat_rate(db: AsyncSession, room_id: UUID, user_id: UUID,
                          slow_mode_seconds: int = 0) -> None:
    now = now_utc()
    times = (await db.scalars(select(ChatMessage.created_at).where(
        ChatMessage.room_id == room_id, ChatMessage.sender_user_id == user_id,
        ChatMessage.created_at >= now - timedelta(seconds=CHAT_MINUTE_SECONDS)
    ).order_by(ChatMessage.created_at.desc()).limit(CHAT_MINUTE_LIMIT))).all()
    if slow_mode_seconds and times and times[0] > now - timedelta(seconds=slow_mode_seconds):
        retry_after = max(1, ceil((times[0] + timedelta(seconds=slow_mode_seconds) - now).total_seconds()))
        raise HTTPException(status_code=429,
                            detail=f"Slow mode: chờ {retry_after} giây trước khi gửi tin tiếp",
                            headers={"Retry-After": str(retry_after)})
    if len(times) >= CHAT_MINUTE_LIMIT:
        raise HTTPException(status_code=429, detail="Gửi tin quá nhanh, vui lòng thử lại sau",
                            headers={"Retry-After": str(CHAT_MINUTE_SECONDS)})
    if sum(created >= now - timedelta(seconds=CHAT_BURST_SECONDS) for created in times) >= CHAT_BURST_LIMIT:
        raise HTTPException(status_code=429, detail="Gửi tin quá nhanh, vui lòng thử lại sau",
                            headers={"Retry-After": str(CHAT_BURST_SECONDS)})


@router.patch("/policy", response_model=ChatSendOutput)
async def update_chat_policy(room_id: UUID, body: ChatPolicyInput,
                             user: User = Depends(current_user),
                             db: AsyncSession = Depends(get_db)) -> ChatSendOutput:
    await require_room_host(db, room_id, user.id)
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    request = body.model_dump(mode="json", exclude={"client_request_id"})
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "chat.policy_changed" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ChatSendOutput(event_id=prior.id, repeated=True)
    if room.status == "finished":
        raise HTTPException(status_code=409, detail="Trận đã kết thúc")
    previous = policy_for(room)
    current = {key: request[key] for key in DEFAULT_CHAT_POLICY}
    if current == previous:
        raise HTTPException(status_code=409, detail="Quyền chat chưa thay đổi")
    room.chat_policy = current
    event = await append_event(db, room_id, user.id, "chat.policy_changed", "room",
                               {"request": request, "previous": previous, "current": current,
                                "reason": body.reason}, client_request_id=body.client_request_id)
    await db.commit()
    return ChatSendOutput(event_id=event.id, repeated=False)


async def role_for(db: AsyncSession, room_id: UUID, user_id: UUID) -> tuple[dict, PlayerState] | None:
    snapshot = await db.get(RoleSnapshot, room_id)
    assignment = await db.get(RoleAssignment, (room_id, user_id))
    state = await db.get(PlayerState, (room_id, user_id))
    if snapshot is None or assignment is None or state is None:
        return None
    definitions = {item["id"]: item for item in snapshot.definitions}
    return effective_role_data(definitions, assignment.role_id, state), state


def policy_for(room: Room) -> dict[str, bool | int]:
    return {**DEFAULT_CHAT_POLICY, **(room.chat_policy or {})}


def common_writable(room: Room) -> bool:
    return not room.is_paused and (room.status in {"open", "closed", "drafting", "assigned"}
                                   or (room.status == "playing" and (
                                       room.phase_type == "day" or policy_for(room)["common_send_at_night"])))


def player_common_writable(room: Room, role_state: tuple[dict, PlayerState] | None) -> bool:
    return common_writable(room) and (room.status != "playing" or bool(
        role_state and ((role_state[1].is_alive and role_state[1].public_is_alive)
                        or policy_for(room)["dead_common_send"])))


def team_writable(room: Room, role_state: tuple[dict, PlayerState] | None) -> bool:
    policy = policy_for(room)
    return bool(room.status == "playing" and not room.is_paused
                and (room.phase_type == "night" or policy["team_send_in_day"])
                and role_state and ((role_state[1].is_alive and role_state[1].public_is_alive)
                                    or policy["dead_team_send"])
                and role_state[0].get("team_chat_label"))


async def host_writable(db: AsyncSession, room: Room, user_id: UUID,
                        role_state: tuple[dict, PlayerState] | None) -> bool:
    if room.status != "playing" or room.is_paused or not role_state:
        return False
    role, state = role_state
    contact = role.get("host_contact") or {}
    can_initiate = (role.get("role_kind") == "active" and contact.get("can_initiate")
                    and room.phase_type in contact.get("initiate_phases", [])
                    and (state.is_alive or contact.get("when_dead")))
    if can_initiate:
        return True
    if not contact.get("reply_allowed", True):
        return False
    opened = await db.scalar(select(ChatMessage.id).where(
        ChatMessage.room_id == room.id, ChatMessage.channel == "host",
        ChatMessage.participant_user_id == user_id,
        ChatMessage.sender_user_id == room.owner_user_id).limit(1))
    if opened is not None:
        return True
    legacy = await db.scalar(select(PrivateMessage.id).where(
        PrivateMessage.room_id == room.id, PrivateMessage.user_id == user_id,
        PrivateMessage.sender_user_id == room.owner_user_id).limit(1))
    return legacy is not None


async def team_roster(db: AsyncSession, room: Room) -> dict[str, list[str]]:
    snapshot = await db.get(RoleSnapshot, room.id)
    if snapshot is None:
        return {}
    definitions = {item["id"]: item for item in snapshot.definitions}
    rows = (await db.execute(select(RoleAssignment, PlayerState).join(
        PlayerState, and_(PlayerState.room_id == RoleAssignment.room_id,
                          PlayerState.user_id == RoleAssignment.user_id))
        .join(RoomMember, and_(RoomMember.room_id == RoleAssignment.room_id,
                               RoomMember.user_id == RoleAssignment.user_id))
        .where(RoleAssignment.room_id == room.id, RoomMember.left_at.is_(None)))).all()
    teams: dict[str, list[str]] = {}
    for assignment, state in rows:
        if (not state.is_alive or not state.public_is_alive) and not policy_for(room)["dead_team_send"]:
            continue
        label = effective_role_data(definitions, assignment.role_id, state).get("team_chat_label")
        if label:
            teams.setdefault(label, []).append(str(assignment.user_id))
    return teams


async def read_chat_context(room_id: UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)) -> ChatContext:
    is_host = await require_room_access(db, room_id, user.id)
    room = await db.get(Room, room_id)
    role_state = None if is_host else await role_for(db, room_id, user.id)
    mute = None if is_host else await db.get(ChatMute, (room_id, user.id))
    is_muted = bool(mute and mute.is_muted)
    muted_players = []
    if is_host:
        muted_players = [{"user_id": str(muted.user_id), "reason": muted.reason}
                         for muted in (await db.scalars(select(ChatMute).where(
                             ChatMute.room_id == room_id, ChatMute.is_muted.is_(True)))).all()]
    current_team = role_state[0].get("team_chat_label") if role_state else None
    host_roster = await team_roster(db, room) if is_host else {}
    writable_teams = (sorted(host_roster) if room.status == "playing" and (
        room.phase_type == "night" or policy_for(room)["team_send_in_day"])
                      and not room.is_paused else []) if is_host else (
        [current_team] if team_writable(room, role_state) and current_team else [])
    if is_host or room.status == "finished":
        teams = set(host_roster)
        teams.update((await db.scalars(select(ChatMessage.team_label).where(
            ChatMessage.room_id == room_id, ChatMessage.channel == "team").distinct())).all())
    else:
        teams = set((await db.scalars(select(ChatMessage.team_label).where(
            ChatMessage.room_id == room_id, ChatMessage.channel == "team",
            ChatMessage.recipient_ids.contains([str(user.id)])).distinct())).all())
        if current_team:
            teams.add(current_team)
    incoming_filters = [ChatMessage.room_id == room_id, ChatMessage.sender_user_id != user.id]
    if not is_host and room.status != "finished":
        incoming_filters.append(or_(
            ChatMessage.channel == "common",
            and_(ChatMessage.channel == "team", ChatMessage.recipient_ids.contains([str(user.id)])),
            and_(ChatMessage.channel == "host", ChatMessage.participant_user_id == user.id),
        ))
    private_filters = [PrivateMessage.room_id == room_id, PrivateMessage.sender_user_id != user.id]
    if not is_host and room.status != "finished":
        private_filters.append(PrivateMessage.user_id == user.id)
    incoming = union_all(select(
        ChatMessage.channel.label("channel"), ChatMessage.team_label.label("team_label"),
        ChatMessage.participant_user_id.label("participant"), ChatMessage.sequence.label("sequence")
    ).outerjoin(ContentRevision, ContentRevision.source_event_id == ChatMessage.event_id)
        .where(*incoming_filters, ContentRevision.deleted_at.is_(None)), select(
        literal("host"), cast(null(), String), PrivateMessage.user_id, PrivateMessage.sequence
    ).outerjoin(ContentRevision, ContentRevision.source_event_id == PrivateMessage.event_id)
        .where(*private_filters, ContentRevision.deleted_at.is_(None))).subquery()
    activity_rows = (await db.execute(select(incoming.c.channel, incoming.c.team_label,
        incoming.c.participant, func.max(incoming.c.sequence)).group_by(
            incoming.c.channel, incoming.c.team_label, incoming.c.participant))).all()
    participants = []
    if (is_host or room.status == "finished") and room.status in {"playing", "finished"}:
        participants = [ChatParticipant(user_id=person.id, display_name=person.display_name,
                                        departed=left_at is not None)
            for person, left_at in (await db.execute(select(User, RoomMember.left_at)
                .join(RoomMember, RoomMember.user_id == User.id)
                .join(PlayerState, and_(PlayerState.room_id == RoomMember.room_id,
                                        PlayerState.user_id == RoomMember.user_id))
                .where(RoomMember.room_id == room_id).order_by(RoomMember.seat_number, User.id))).all()]
    slow_seconds = 0 if is_host else int(policy_for(room)["slow_mode_seconds"])
    last_chat_at = await db.scalar(select(ChatMessage.created_at).where(
        ChatMessage.room_id == room_id, ChatMessage.sender_user_id == user.id
    ).order_by(ChatMessage.created_at.desc()).limit(1)) if slow_seconds else None
    return ChatContext(team_labels=sorted(label for label in teams if label),
                       writable_team_labels=writable_teams,
                       current_team_label=current_team,
                       policy=policy_for(room),
                       next_chat_at=last_chat_at + timedelta(seconds=slow_seconds) if last_chat_at else None,
                       can_send_common=(common_writable(room) if is_host else player_common_writable(room, role_state)) and not is_muted,
                       can_send_team=bool(writable_teams) and not is_muted,
                       can_send_host=(room.status == "playing" and not room.is_paused) if is_host
                       else await host_writable(db, room, user.id, role_state) and not is_muted,
                       is_muted=is_muted, mute_reason=mute.reason if is_muted else None,
                       muted_players=muted_players,
                       dm_participants=participants,
                       activity=[ChatActivity(channel=channel, team_label=label,
                                              participant_user_id=participant,
                                              sequence=sequence)
                                 for channel, label, participant, sequence in activity_rows])


@router.get("/context", response_model=ChatContext)
async def chat_context(room_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    context=await read_chat_context(room_id,user,db)
    room=await db.get(Room,room_id)
    if user.id!=room.owner_user_id and room.status!='finished':
        context=context.model_copy(update={'activity':[]})
    return context


@router.get("/messages", response_model=ChatPage)
async def chat_messages(room_id: UUID, channel: Channel,
                        before_sequence: int | None = Query(default=None, ge=1),
                        after_sequence: int | None = Query(default=None, ge=0),
                        after_updated_sequence: int | None = Query(default=None, ge=0),
                        limit: int = Query(default=30, ge=1, le=50),
                        team_label: str | None = Query(default=None, max_length=80),
                        participant_user_id: UUID | None = None,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> ChatPage:
    from app.legacy_protocol import require_legacy_reader
    await require_legacy_reader(db,room_id,user)
    # Compatibility for existing clients: this endpoint still contains ChatMessage only.
    return await read_chat_page(db, user, room_id, channel, before_sequence, after_sequence,
                                after_updated_sequence, limit, team_label, participant_user_id,
                                include_private=False)


@router.get("/timeline", response_model=ChatPage)
async def chat_timeline(room_id: UUID, channel: Channel,
                        before_sequence: int | None = Query(default=None, ge=1),
                        after_sequence: int | None = Query(default=None, ge=0),
                        after_updated_sequence: int | None = Query(default=None, ge=0),
                        limit: int = Query(default=30, ge=1, le=50),
                        team_label: str | None = Query(default=None, max_length=80),
                        participant_user_id: UUID | None = None,
                        channel_id: UUID | None = None,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> ChatPage:
    from app.legacy_protocol import require_legacy_reader
    await require_legacy_reader(db,room_id,user)
    if channel_id is not None:
        from app.channels import resolve_channel
        ref = await resolve_channel(db, room_id, channel_id, user)
        expected = "host" if ref.kind == "moderator_dm" else ref.kind
        if expected != channel or (team_label is not None and team_label != ref.team_label) or (
                participant_user_id is not None and participant_user_id != ref.participant_user_id):
            raise HTTPException(status_code=422, detail="Bộ lọc không thuộc kênh này")
        team_label = ref.team_label
        # The legacy reader intentionally rejects even an explicit self selector from players.
        room = await db.get(Room, room_id)
        if room.owner_user_id == user.id or room.status == "finished":
            participant_user_id = ref.participant_user_id
    return await read_chat_page(db, user, room_id, channel, before_sequence, after_sequence,
                                after_updated_sequence, limit, team_label, participant_user_id,
                                include_private=True)


async def read_chat_page(db: AsyncSession, user: User, room_id: UUID, channel: Channel,
                         before_sequence: int | None, after_sequence: int | None,
                         after_updated_sequence: int | None, limit: int,
                         team_label: str | None, participant_user_id: UUID | None,
                         *, include_private: bool, include_tombstones: bool = False,
                         anchor_item: tuple[str, UUID] | None = None, include_requests: bool = False) -> ChatPage:
    is_host = await require_room_access(db, room_id, user.id)
    if sum(value is not None for value in (before_sequence, after_sequence, after_updated_sequence)) > 1:
        raise HTTPException(status_code=422, detail="Chỉ dùng một hướng phân trang")
    if (team_label is not None and channel != "team") or (participant_user_id is not None and channel != "host"):
        raise HTTPException(status_code=422, detail="Bộ lọc không thuộc kênh này")
    room = await db.get(Room, room_id)
    finished = room.status == "finished"
    if not is_host and not finished and participant_user_id is not None:
        raise HTTPException(status_code=403, detail="Không được chọn cuộc trò chuyện của người khác")
    filters = [ChatMessage.room_id == room_id, ChatMessage.channel == channel]
    private_filters = [PrivateMessage.room_id == room_id]
    if channel == "team":
        if not is_host and not finished:
            filters.append(ChatMessage.recipient_ids.contains([str(user.id)]))
        if team_label is not None:
            filters.append(ChatMessage.team_label == team_label)
    elif channel == "host":
        if is_host and not finished:
            if participant_user_id is None or await db.get(PlayerState, (room_id, participant_user_id)) is None:
                raise HTTPException(status_code=422, detail="Chọn người chơi trong trận")
        participant = participant_user_id if is_host or finished else user.id
        if participant is not None:
            filters.append(ChatMessage.participant_user_id == participant)
            private_filters.append(PrivateMessage.user_id == participant)

    # Union before applying limit: sources interleave by their committed event position,
    # even when timestamps or UUIDs collide. Reads never copy or create messages.
    chats = select(
        ChatMessage.id, ChatMessage.event_id, ChatMessage.sequence, ChatMessage.updated_sequence,
        ChatMessage.channel, ChatMessage.team_label, ChatMessage.participant_user_id,
        ChatMessage.sender_user_id, ChatMessage.text, ChatMessage.created_at,
        literal("chat").label("source_kind")).where(*filters)
    if include_private and channel == "host":
        privates = select(
            PrivateMessage.id, PrivateMessage.event_id, PrivateMessage.sequence, PrivateMessage.updated_sequence,
            literal("host"), cast(null(), String), PrivateMessage.user_id,
            PrivateMessage.sender_user_id, PrivateMessage.text, PrivateMessage.created_at,
            literal("private_message")).where(*private_filters)
        sources = [chats, privates]
        if include_requests:
            request_filters = [RoleRequest.room_id == room_id]
            if participant is not None:
                request_filters.append(RoleRequest.user_id == participant)
            sources.append(select(RoleRequest.id, RoleRequest.event_id, RoleRequest.sequence, RoleRequest.updated_sequence,
                literal('host'), cast(null(), String), RoleRequest.user_id, RoleRequest.user_id,
                RoleRequest.text, RoleRequest.created_at, literal('role_request')).where(*request_filters))
        source = union_all(*sources).subquery()
    else:
        source = chats.subquery()

    # Compute a scoped high-water mark and page in one database snapshot. Revisions and
    # source rows commit together; a racing edit/create is either here or in changes.
    visible_cursor = select(func.coalesce(func.max(source.c.updated_sequence), 0)).scalar_subquery()
    anchor_sequence = None
    if anchor_item is not None:
        anchor_query = select(source.c.sequence).outerjoin(
            ContentRevision, ContentRevision.source_event_id == source.c.event_id).where(
                source.c.source_kind == anchor_item[0], source.c.id == anchor_item[1])
        if not finished:
            anchor_query = anchor_query.where(ContentRevision.deleted_at.is_(None))
        anchor_sequence = anchor_query.correlate(None).scalar_subquery()
    page_filters = []
    if anchor_sequence is not None:
        page_filters.append(source.c.sequence >= anchor_sequence)
    if before_sequence is not None:
        page_filters.append(source.c.sequence < before_sequence)
    if after_sequence is not None:
        page_filters.append(source.c.sequence > after_sequence)
    if after_updated_sequence is not None:
        page_filters.append(source.c.updated_sequence > after_updated_sequence)
    if after_updated_sequence is None and not finished and not include_tombstones:
        page_filters.append(ContentRevision.deleted_at.is_(None))
    ascending = after_sequence is not None or after_updated_sequence is not None or anchor_item is not None
    sort_column = source.c.updated_sequence if after_updated_sequence is not None else source.c.sequence
    page = select(source, User.display_name.label("sender_name"),
                  ContentRevision.current_text, ContentRevision.edited_at, ContentRevision.deleted_at,
                  GameEvent.payload["phase_type"].as_string().label("phase_type"),
                  GameEvent.payload["round_number"].as_integer().label("round_number"))\
        .join(User, User.id == source.c.sender_user_id).join(GameEvent, GameEvent.id == source.c.event_id)\
        .outerjoin(ContentRevision, ContentRevision.source_event_id == source.c.event_id)\
        .where(*page_filters).order_by(sort_column.asc() if ascending else sort_column.desc()).limit(limit + 1).cte("timeline_page")
    # OUTER JOIN retains the cursor for an empty page (including all-deleted channels).
    boundary = select(visible_cursor.label("cursor"),
        (anchor_sequence if anchor_sequence is not None else literal(0)).label("anchor_sequence")).cte("timeline_boundary")
    page_sort = page.c.updated_sequence if after_updated_sequence is not None else page.c.sequence
    rows = (await db.execute(select(page, boundary.c.cursor, boundary.c.anchor_sequence).select_from(
        boundary.outerjoin(page, true())).order_by(page_sort.asc() if ascending else page_sort.desc()))).mappings().all()
    cursor = rows[0]["cursor"]
    if anchor_item is not None and rows[0]["anchor_sequence"] is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tin được phép xem trong kênh")
    rows = [row for row in rows if row["id"] is not None]
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    ordered = page_rows if ascending else list(reversed(page_rows))
    history_by_source: dict[str, list[dict]] = {}
    if finished and page_rows:
        source_ids = [str(item["event_id"]) for item in page_rows]
        revisions = (await db.scalars(select(GameEvent).where(
            GameEvent.room_id == room_id, GameEvent.kind == "content.revision_decided",
            GameEvent.payload["request"]["source_event_id"].astext.in_(source_ids)
        ).order_by(GameEvent.sequence))).all()
        for revision in revisions:
            request = revision.payload["request"]
            history_by_source.setdefault(request["source_event_id"], []).append({
                "sequence": revision.sequence, "action": request["action"],
                "text": revision.payload.get("new_text"), "created_at": revision.created_at.isoformat()})
    messages = [ChatMessageOutput(
        id=item["id"], event_id=item["event_id"], source_kind=item["source_kind"],
        sequence=item["sequence"], updated_sequence=item["updated_sequence"],
        channel=item["channel"], team_label=item["team_label"], participant_user_id=item["participant_user_id"],
        sender_user_id=item["sender_user_id"], sender_name=item["sender_name"],
        text="" if item["deleted_at"] else item["current_text"] if item["edited_at"] else item["text"],
        created_at=item["created_at"], edited_at=item["edited_at"], deleted=bool(item["deleted_at"]),
        phase_type=item["phase_type"], round_number=item["round_number"],
        history=([{"sequence": item["sequence"], "action": "send", "text": item["text"],
                   "created_at": item["created_at"].isoformat()},
                  *history_by_source.get(str(item["event_id"]), [])] if finished else None)) for item in ordered]
    return ChatPage(messages=messages,
                    next_before_sequence=(page_rows[0]["sequence"] if anchor_item is not None and page_rows else
                        page_rows[-1]["sequence"] if has_more and not ascending else None),
                    next_after_sequence=page_rows[-1]["sequence"] if has_more and (after_sequence is not None or anchor_item is not None) else None,
                    next_after_updated_sequence=page_rows[-1]["updated_sequence"] if has_more and after_updated_sequence is not None else None,
                    room_cursor=cursor)


@router.post("/messages", response_model=ChatSendOutput)
async def send_chat(room_id: UUID, body: ChatSendInput,
                    user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)) -> ChatSendOutput:
    is_host = await require_room_access(db, room_id, user.id)
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    request = body.model_dump(mode="json", exclude={"client_request_id"})
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "chat.sent" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ChatSendOutput(event_id=prior.id, repeated=True)
    if room.status == "finished" or room.is_paused:
        raise HTTPException(status_code=409, detail="Không thể gửi chat lúc này")
    if not is_host:
        mute = await db.get(ChatMute, (room_id, user.id))
        if mute and mute.is_muted:
            raise HTTPException(status_code=403, detail="Quản trò đã tạm chặn quyền gửi chat")
    await check_chat_rate(db, room_id, user.id, 0 if is_host else int(policy_for(room)["slow_mode_seconds"]))
    team_label = None
    participant_id = None
    recipients: list[str] = []
    if body.channel == "common":
        role_state = None if is_host else await role_for(db, room_id, user.id)
        if not (common_writable(room) if is_host else player_common_writable(room, role_state)):
            raise HTTPException(status_code=403, detail="Không có quyền gửi chat chung lúc này")
    elif body.channel == "team":
        if room.status != "playing" or not (room.phase_type == "night" or policy_for(room)["team_send_in_day"]):
            raise HTTPException(status_code=403, detail="Chat đội chưa mở ở pha này")
        roster = await team_roster(db, room)
        if is_host:
            team_label = body.team_label
            if not team_label or team_label not in roster:
                raise HTTPException(status_code=422, detail="Chọn đội chat đang có người sống")
        else:
            if body.team_label is not None:
                raise HTTPException(status_code=403, detail="Không được chọn đội chat")
            role_state = await role_for(db, room_id, user.id)
            if not team_writable(room, role_state):
                raise HTTPException(status_code=403, detail="Không có quyền gửi chat đội")
            team_label = role_state[0]["team_chat_label"]
        recipients = roster[team_label]
    else:
        if room.status != "playing":
            raise HTTPException(status_code=403, detail="Chat quản trò chỉ có trong trận")
        if is_host:
            participant_id = body.participant_user_id
            if participant_id is None or await db.get(PlayerState, (room_id, participant_id)) is None:
                raise HTTPException(status_code=422, detail="Chọn người chơi trong trận")
            member = await db.get(RoomMember, (room_id, participant_id))
            if member is None or member.left_at is not None:
                raise HTTPException(status_code=409, detail="Người chơi đã rời phòng; hội thoại chỉ đọc")
        else:
            if body.participant_user_id is not None:
                raise HTTPException(status_code=403, detail="Không được chọn cuộc trò chuyện của người khác")
            role_state = await role_for(db, room_id, user.id)
            if not await host_writable(db, room, user.id, role_state):
                raise HTTPException(status_code=403, detail="Role không được nhắn quản trò lúc này")
            participant_id = user.id
    payload = {"request": request, "channel": body.channel,
               "team_label": team_label, "participant_user_id": str(participant_id) if participant_id else None,
               "recipient_ids": recipients, "phase_type": room.phase_type,
               "round_number": room.round_number}
    event = await append_event(db, room_id, user.id, "chat.sent",
                               "room" if body.channel == "common" else "host", payload,
                               client_request_id=body.client_request_id)
    db.add(ChatMessage(room_id=room_id, event_id=event.id, sequence=event.sequence,
                       updated_sequence=event.sequence,
                       channel=body.channel, team_label=team_label,
                       participant_user_id=participant_id, sender_user_id=user.id,
                       recipient_ids=recipients, text=body.text))
    await db.commit()
    return ChatSendOutput(event_id=event.id, repeated=False)
