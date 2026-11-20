"""Channel discovery and addressed sends. UUID knowledge never grants access."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import POSTGAME_POLICY_VERSION, current_user, require_room_access
from app.chat import ChatContext, ChatSendInput, read_chat_context, role_for, send_chat
from app.db.models import ChatMessage, GameEvent, PrivateMessage, Room, RoomChannel, RoomMember, User
from app.db.session import get_db
from app.role_requests import block_reason
from app.chat_reads import unread_channels

router = APIRouter(prefix="/api/v2/rooms/{room_id}/channels", tags=["channels"])
Kind = Literal["common", "team", "moderator_dm", "announcement", "vote"]


class Capabilities(BaseModel):
    read: bool = True
    send: bool
    react: bool = False
    submit_request: bool = False


class ChannelRef(BaseModel):
    id: UUID
    kind: Kind
    name: str
    capabilities: Capabilities
    readonly_reason: str | None
    team_label: str | None = None
    participant_user_id: UUID | None = None
    departed: bool = False
    last_activity_id: str | None = None
    unread: bool = False


class ChannelCatalog(BaseModel):
    channels: list[ChannelRef]


class AddressedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def normalize(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Cần nội dung tin nhắn")
        return self


class MessageAck(BaseModel):
    source_kind: Literal["chat"] = "chat"
    id: UUID
    repeated: bool


async def visible_channels(db: AsyncSession, room_id: UUID, user: User) -> list[ChannelRef]:
    # Reuse the same policy helpers as the legacy send service. A catalog read has no writes/events.
    context = await read_chat_context(room_id, user, db)
    room = await db.get(Room, room_id)
    host = room.owner_user_id == user.id
    finished = room.status == "finished"
    unread = set() if finished else await unread_channels(db, room_id, user.id)
    role_state = None if host else await role_for(db, room_id, user.id)
    people = {str(person.user_id): person for person in context.dm_participants}
    positions = [item.sequence for item in context.activity]
    sources = (await db.execute(union_all(
        select(ChatMessage.sequence, literal('chat'), ChatMessage.id).where(
            ChatMessage.room_id == room_id, ChatMessage.sequence.in_(positions)),
        select(PrivateMessage.sequence, literal('private_message'), PrivateMessage.id).where(
            PrivateMessage.room_id == room_id, PrivateMessage.sequence.in_(positions))))).all() if positions else []
    source_ids = {sequence: f'{kind}:{source_id}' for sequence, kind, source_id in sources}
    activity = {(item.channel, item.team_label, item.participant_user_id): source_ids.get(item.sequence)
                for item in context.activity}
    channels = (await db.scalars(select(RoomChannel).where(RoomChannel.room_id == room_id)
                                .order_by(RoomChannel.kind, RoomChannel.channel_key))).all()
    result = []
    for channel in channels:
        kind, key = channel.kind, channel.channel_key
        participant = None
        departed = False
        can_send = False
        submit_request = False
        if kind == "common":
            name, can_send = "general", context.can_send_common
        elif kind == "team":
            if key not in context.team_labels:
                continue
            name = key
            can_send = context.can_send_team and key in context.writable_team_labels
        elif kind == "moderator_dm":
            if room.status not in {"playing", "finished"}:
                continue
            participant = UUID(key)
            if host or finished:
                person = people.get(key)
                if person is None:
                    continue
                departed = person.departed
                name = person.display_name + (" · đã rời" if departed else "")
            elif participant == user.id:
                name = "Quản trò"
                submit_request = block_reason(room, role_state) is None
            else:
                continue
            can_send = context.can_send_host and not departed
        else:
            name = "Thông báo" if kind == "announcement" else "Vote"
        reason = readonly_reason(kind, key, room, context, host, departed) if not can_send else None
        result.append(ChannelRef(id=channel.id, kind=kind, name=name,
            unread=channel.id in unread,
            capabilities=Capabilities(send=can_send, react=can_send and kind in {'common','team','moderator_dm'}, submit_request=submit_request), readonly_reason=reason,
            team_label=key if kind == "team" else None, participant_user_id=participant, departed=departed,
            last_activity_id=activity.get(('host' if kind == 'moderator_dm' else kind,
                                           key if kind == 'team' else None, participant))))
    return result


def readonly_reason(kind: str, key: str, room: Room, context: ChatContext, host: bool, departed: bool) -> str:
    if room.status == "finished":
        return "Ván đã kết thúc · Lịch sử chỉ đọc."
    if kind == "announcement":
        return "Thông báo dùng công cụ đăng riêng của quản trò."
    if kind == "vote":
        return "Dùng biểu mẫu vote khi quản trò mở vòng."
    if departed:
        return "Người chơi đã rời phòng · Hội thoại chỉ đọc."
    if room.is_paused:
        return "Ván đang tạm dừng. Bạn vẫn đọc được lịch sử."
    if context.is_muted:
        return "Bạn đang bị chặn gửi chat. Lịch sử vẫn đọc được."
    if kind == "common":
        return "Chat chung chỉ gửi ban ngày." if room.phase_type == "night" and not context.policy["common_send_at_night"] else "Trạng thái hiện tại của bạn chỉ cho phép đọc chat chung."
    if kind == "team":
        if not host and key != context.current_team_label:
            return "Đây là đội cũ · Bạn chỉ đọc được những tin đã nhận."
        if room.status != "playing":
            return "Chat đội mở khi ván đang diễn ra."
        if room.phase_type == "day" and not context.policy["team_send_in_day"]:
            return "Chat đội chỉ gửi ban đêm."
        return "Trạng thái hiện tại chưa cho phép gửi vào đội này."
    if room.status != "playing":
        return "Chat quản trò mở khi ván đang diễn ra."
    return "Role hoặc pha hiện tại chưa cho phép bạn nhắn quản trò. Bạn vẫn đọc được phản hồi."


async def resolve_channel(db: AsyncSession, room_id: UUID, channel_id: UUID, user: User) -> ChannelRef:
    channel = next((item for item in await visible_channels(db, room_id, user) if item.id == channel_id), None)
    if channel is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh được phép xem")
    return channel


@router.get("", response_model=ChannelCatalog)
async def list_channels(room_id: UUID, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> ChannelCatalog:
    return ChannelCatalog(channels=await visible_channels(db, room_id, user))


@router.post("/{channel_id}/messages", response_model=MessageAck)
async def send_addressed_message(room_id: UUID, channel_id: UUID, body: AddressedMessage,
                                  user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> MessageAck:
    host = await require_room_access(db, room_id, user.id)
    # Policy check and actual send share the room lock: a stale team UUID cannot send into a new team.
    room = await db.scalar(select(Room).where(Room.id == room_id).with_for_update()
                           .execution_options(populate_existing=True))
    if not host:
        member = await db.scalar(select(RoomMember).where(RoomMember.room_id == room_id,
            RoomMember.user_id == user.id).execution_options(populate_existing=True))
        if (member is None or member.consent_version != POSTGAME_POLICY_VERSION
                or (member.left_at is not None and room.status != "finished")):
            raise HTTPException(status_code=403, detail="Không có quyền vào phòng")
    channel = await resolve_channel(db, room_id, channel_id, user)
    if channel.kind not in {"common", "team", "moderator_dm"}:
        raise HTTPException(status_code=409, detail="Kênh này không nhận tin nhắn thường")
    kind = "host" if channel.kind == "moderator_dm" else channel.kind
    prior = await db.scalar(select(GameEvent).where(GameEvent.room_id == room_id,
        GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        message = await db.scalar(select(ChatMessage).where(ChatMessage.room_id == room_id,
            ChatMessage.event_id == prior.id, ChatMessage.sender_user_id == user.id))
        if (prior.kind != "chat.sent" or prior.actor_user_id != user.id or message is None
            or message.text != body.text or message.channel != kind
            or message.team_label != channel.team_label or message.participant_user_id != channel.participant_user_id):
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return MessageAck(id=message.id, repeated=True)
    if not channel.capabilities.send:
        raise HTTPException(status_code=403, detail=channel.readonly_reason)
    result = await send_chat(room_id, ChatSendInput(client_request_id=body.client_request_id,
        text=body.text, channel=kind, team_label=channel.team_label if host else None,
        participant_user_id=channel.participant_user_id if host else None), user, db)
    message = await db.scalar(select(ChatMessage).where(ChatMessage.event_id == result.event_id))
    return MessageAck(id=message.id, repeated=result.repeated)
