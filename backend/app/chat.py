"""Persistent room chat; every read and send is authorized from current DB state."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access, require_room_host
from app.db.events import append_event
from app.db.models import (ChatMessage, GameEvent, PlayerState, PrivateMessage,
                           RoleAssignment, RoleSnapshot, Room, User)
from app.db.session import get_db
from app.roles import effective_role_data

router = APIRouter(prefix="/api/rooms/{room_id}/chat", tags=["chat"])
Channel = Literal["common", "team", "host"]
DEFAULT_CHAT_POLICY = {
    "common_send_at_night": False,
    "team_send_in_day": False,
    "dead_common_send": False,
    "dead_team_send": False,
}


class ChatPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    common_send_at_night: bool
    team_send_in_day: bool
    dead_common_send: bool
    dead_team_send: bool
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_reason(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("Cần lý do đổi quyền chat")
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
    id: UUID
    event_id: UUID
    sequence: int
    channel: Channel
    team_label: str | None
    participant_user_id: UUID | None
    sender_user_id: UUID
    sender_name: str
    text: str
    created_at: datetime


class ChatPage(BaseModel):
    messages: list[ChatMessageOutput]
    next_before_sequence: int | None
    next_after_sequence: int | None = None


class ChatContext(BaseModel):
    team_labels: list[str]
    writable_team_labels: list[str]
    current_team_label: str | None
    policy: dict[str, bool]
    can_send_common: bool
    can_send_team: bool
    can_send_host: bool


class ChatSendOutput(BaseModel):
    event_id: UUID
    repeated: bool


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


def policy_for(room: Room) -> dict[str, bool]:
    return {**DEFAULT_CHAT_POLICY, **(room.chat_policy or {})}


def common_writable(room: Room) -> bool:
    return not room.is_paused and (room.status in {"open", "closed", "drafting", "assigned"}
                                   or (room.status == "playing" and (
                                       room.phase_type == "day" or policy_for(room)["common_send_at_night"])))


def player_common_writable(room: Room, role_state: tuple[dict, PlayerState] | None) -> bool:
    return common_writable(room) and (room.status != "playing" or bool(
        role_state and (role_state[1].is_alive or policy_for(room)["dead_common_send"])))


def team_writable(room: Room, role_state: tuple[dict, PlayerState] | None) -> bool:
    policy = policy_for(room)
    return bool(room.status == "playing" and not room.is_paused
                and (room.phase_type == "night" or policy["team_send_in_day"])
                and role_state and (role_state[1].is_alive or policy["dead_team_send"])
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
        .where(RoleAssignment.room_id == room.id))).all()
    teams: dict[str, list[str]] = {}
    for assignment, state in rows:
        if not state.is_alive and not policy_for(room)["dead_team_send"]:
            continue
        label = effective_role_data(definitions, assignment.role_id, state).get("team_chat_label")
        if label:
            teams.setdefault(label, []).append(str(assignment.user_id))
    return teams


@router.get("/context", response_model=ChatContext)
async def chat_context(room_id: UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)) -> ChatContext:
    is_host = await require_room_access(db, room_id, user.id)
    room = await db.get(Room, room_id)
    role_state = None if is_host else await role_for(db, room_id, user.id)
    current_team = role_state[0].get("team_chat_label") if role_state else None
    host_roster = await team_roster(db, room) if is_host else {}
    writable_teams = (sorted(host_roster) if room.status == "playing" and (
        room.phase_type == "night" or policy_for(room)["team_send_in_day"])
                      and not room.is_paused else []) if is_host else (
        [current_team] if team_writable(room, role_state) and current_team else [])
    if is_host:
        teams = set(host_roster)
        teams.update((await db.scalars(select(ChatMessage.team_label).where(
            ChatMessage.room_id == room_id, ChatMessage.channel == "team").distinct())).all())
    else:
        teams = set((await db.scalars(select(ChatMessage.team_label).where(
            ChatMessage.room_id == room_id, ChatMessage.channel == "team",
            ChatMessage.recipient_ids.contains([str(user.id)])).distinct())).all())
        if current_team:
            teams.add(current_team)
    return ChatContext(team_labels=sorted(label for label in teams if label),
                       writable_team_labels=writable_teams,
                       current_team_label=current_team,
                       policy=policy_for(room),
                       can_send_common=common_writable(room) if is_host else player_common_writable(room, role_state),
                       can_send_team=bool(writable_teams),
                       can_send_host=(room.status == "playing" and not room.is_paused) if is_host
                       else await host_writable(db, room, user.id, role_state))


@router.get("/messages", response_model=ChatPage)
async def chat_messages(room_id: UUID, channel: Channel,
                        before_sequence: int | None = Query(default=None, ge=1),
                        after_sequence: int | None = Query(default=None, ge=0),
                        limit: int = Query(default=30, ge=1, le=50),
                        team_label: str | None = Query(default=None, max_length=80),
                        participant_user_id: UUID | None = None,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> ChatPage:
    is_host = await require_room_access(db, room_id, user.id)
    if before_sequence is not None and after_sequence is not None:
        raise HTTPException(status_code=422, detail="Chỉ dùng một hướng phân trang")
    if not is_host and (team_label is not None or participant_user_id is not None):
        raise HTTPException(status_code=403, detail="Không được chọn cuộc trò chuyện của người khác")
    filters = [ChatMessage.room_id == room_id, ChatMessage.channel == channel]
    if before_sequence is not None:
        filters.append(ChatMessage.sequence < before_sequence)
    if after_sequence is not None:
        filters.append(ChatMessage.sequence > after_sequence)
    if channel == "team":
        if is_host:
            if team_label is not None:
                filters.append(ChatMessage.team_label == team_label)
        else:
            filters.append(ChatMessage.recipient_ids.contains([str(user.id)]))
    elif channel == "host":
        if is_host:
            if participant_user_id is None or await db.get(PlayerState, (room_id, participant_user_id)) is None:
                raise HTTPException(status_code=422, detail="Chọn người chơi trong trận")
            filters.append(ChatMessage.participant_user_id == participant_user_id)
        else:
            filters.append(ChatMessage.participant_user_id == user.id)
    elif team_label is not None or participant_user_id is not None:
        raise HTTPException(status_code=422, detail="Kênh chung không có bộ lọc riêng")
    rows = (await db.execute(select(ChatMessage, User.display_name).join(
        User, User.id == ChatMessage.sender_user_id).where(*filters)
        .order_by(ChatMessage.sequence.asc() if after_sequence is not None
                  else ChatMessage.sequence.desc()).limit(limit + 1))).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    ordered = page if after_sequence is not None else list(reversed(page))
    messages = [ChatMessageOutput(
        id=message.id, event_id=message.event_id, sequence=message.sequence,
        channel=message.channel, team_label=message.team_label,
        participant_user_id=message.participant_user_id,
        sender_user_id=message.sender_user_id, sender_name=sender_name,
        text=message.text, created_at=message.created_at)
        for message, sender_name in ordered]
    return ChatPage(messages=messages,
                    next_before_sequence=page[-1][0].sequence if has_more and after_sequence is None else None,
                    next_after_sequence=page[-1][0].sequence if has_more and after_sequence is not None else None)


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
                       channel=body.channel, team_label=team_label,
                       participant_user_id=participant_id, sender_user_id=user.id,
                       recipient_ids=recipients, text=body.text))
    await db.commit()
    return ChatSendOutput(event_id=event.id, repeated=False)
