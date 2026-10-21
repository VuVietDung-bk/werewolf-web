"""Private, free-text player requests for moderator adjudication."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, now_utc, require_room_access, require_room_host
from app.db.events import append_event
from app.db.models import (GameEvent, PlayerState, RoleAssignment, RoleRequest,
                           RoleSnapshot, Room, User)
from app.db.session import get_db
from app.roles import effective_role_data

router = APIRouter(prefix="/api/rooms/{room_id}/role-requests", tags=["role-requests"])
LINKABLE_KINDS = {"player.kill", "player.revive", "player.add_effect", "player.remove_effect",
                  "player.change_role", "player.change_faction", "player.change_team"}
REQUEST_BURST_LIMIT = 3
REQUEST_BURST_SECONDS = 30
REQUEST_WINDOW_LIMIT = 10
REQUEST_WINDOW_SECONDS = 300


class RoleRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def valid_text(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Cần nội dung yêu cầu")
        return self


class RoleRequestContext(BaseModel):
    can_submit: bool
    reason: str | None
    role_name: str | None
    allowed_phases: list[str]
    request_when_dead: bool
    is_alive: bool | None


class RoleRequestOutput(BaseModel):
    id: UUID
    event_id: UUID
    sequence: int
    user_id: UUID
    sender_name: str
    role_id: UUID
    role_name: str
    phase_type: str
    round_number: int
    text: str
    status: str
    reply_text: str | None
    linked_event_ids: list[UUID]
    updated_sequence: int
    created_at: datetime
    updated_at: datetime


class RoleRequestPage(BaseModel):
    requests: list[RoleRequestOutput]
    next_before_sequence: int | None
    next_after_sequence: int | None = None
    room_cursor: int


class RoleRequestSendOutput(BaseModel):
    id: UUID
    event_id: UUID
    repeated: bool


class RoleRequestReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    status: Literal["in_review", "accepted", "rejected"]
    reply_text: str | None = Field(default=None, max_length=2000)
    linked_event_ids: list[UUID] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_fields(self):
        self.reply_text = (self.reply_text or "").strip() or None
        if self.status in {"accepted", "rejected"} and not self.reply_text:
            raise ValueError("Cần phản hồi khi chấp nhận hoặc từ chối")
        if self.status != "accepted" and self.linked_event_ids:
            raise ValueError("Chỉ yêu cầu được chấp nhận mới liên kết thao tác")
        if len(set(self.linked_event_ids)) != len(self.linked_event_ids):
            raise ValueError("Thao tác liên kết bị trùng")
        return self


class RoleRequestWithdrawInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID


class LinkableAction(BaseModel):
    event_id: UUID
    sequence: int
    kind: str
    target_name: str
    created_at: datetime


async def current_role(db: AsyncSession, room_id: UUID,
                       user_id: UUID) -> tuple[dict, PlayerState] | None:
    snapshot = await db.get(RoleSnapshot, room_id)
    assignment = await db.get(RoleAssignment, (room_id, user_id))
    state = await db.get(PlayerState, (room_id, user_id))
    if snapshot is None or assignment is None or state is None:
        return None
    definitions = {item["id"]: item for item in snapshot.definitions}
    return effective_role_data(definitions, assignment.role_id, state), state


def block_reason(room: Room, role_state: tuple[dict, PlayerState] | None) -> str | None:
    if room.status != "playing":
        return "Chỉ gửi yêu cầu khi trận đang diễn ra"
    if room.is_paused:
        return "Trận đang tạm dừng"
    if role_state is None:
        return "Bạn chưa có role trong ván"
    role, state = role_state
    if role.get("role_kind") != "active":
        return "Role bị động không thể tự gửi yêu cầu"
    if room.phase_type not in role.get("request_phases", []):
        return "Role không được gửi yêu cầu ở pha này"
    if not state.is_alive and not role.get("request_when_dead", False):
        return "Role không được gửi yêu cầu sau khi chết"
    return None


async def check_rate(db: AsyncSession, room_id: UUID, user_id: UUID) -> None:
    now = now_utc()
    times = (await db.scalars(select(RoleRequest.created_at).where(
        RoleRequest.room_id == room_id, RoleRequest.user_id == user_id,
        RoleRequest.created_at >= now - timedelta(seconds=REQUEST_WINDOW_SECONDS)
    ).order_by(RoleRequest.created_at.desc()).limit(REQUEST_WINDOW_LIMIT))).all()
    if len(times) >= REQUEST_WINDOW_LIMIT:
        raise HTTPException(status_code=429, detail="Gửi yêu cầu quá nhanh, vui lòng thử lại sau",
                            headers={"Retry-After": str(REQUEST_WINDOW_SECONDS)})
    if sum(created >= now - timedelta(seconds=REQUEST_BURST_SECONDS) for created in times) >= REQUEST_BURST_LIMIT:
        raise HTTPException(status_code=429, detail="Gửi yêu cầu quá nhanh, vui lòng thử lại sau",
                            headers={"Retry-After": str(REQUEST_BURST_SECONDS)})


@router.get("/context", response_model=RoleRequestContext)
async def role_request_context(room_id: UUID, user: User = Depends(current_user),
                               db: AsyncSession = Depends(get_db)) -> RoleRequestContext:
    is_host = await require_room_access(db, room_id, user.id)
    room = await db.get(Room, room_id)
    role_state = None if is_host else await current_role(db, room_id, user.id)
    role = role_state[0] if role_state else None
    reason = "Quản trò xem yêu cầu của người chơi" if is_host else block_reason(room, role_state)
    return RoleRequestContext(can_submit=reason is None, reason=reason,
                              role_name=role.get("name") if role else None,
                              allowed_phases=role.get("request_phases", []) if role else [],
                              request_when_dead=bool(role and role.get("request_when_dead")),
                              is_alive=role_state[1].is_alive if role_state else None)


@router.get("", response_model=RoleRequestPage)
async def list_role_requests(room_id: UUID,
                             before_sequence: int | None = Query(default=None, ge=1),
                             after_sequence: int | None = Query(default=None, ge=0),
                             after_updated_sequence: int | None = Query(default=None, ge=0),
                             limit: int = Query(default=30, ge=1, le=50),
                             user: User = Depends(current_user),
                             db: AsyncSession = Depends(get_db)) -> RoleRequestPage:
    is_host = await require_room_access(db, room_id, user.id)
    if sum(value is not None for value in (before_sequence, after_sequence, after_updated_sequence)) > 1:
        raise HTTPException(status_code=422, detail="Chỉ dùng một hướng phân trang")
    room = await db.get(Room, room_id)
    filters = [RoleRequest.room_id == room_id]
    if not is_host:
        filters.append(RoleRequest.user_id == user.id)
    if before_sequence is not None:
        filters.append(RoleRequest.sequence < before_sequence)
    if after_sequence is not None:
        filters.append(RoleRequest.sequence > after_sequence)
    if after_updated_sequence is not None:
        filters.append(RoleRequest.updated_sequence > after_updated_sequence)
    ascending = after_sequence is not None or after_updated_sequence is not None
    order_column = RoleRequest.updated_sequence if after_updated_sequence is not None else RoleRequest.sequence
    rows = (await db.execute(select(RoleRequest, User.display_name).join(
        User, User.id == RoleRequest.user_id).where(*filters)
        .order_by(order_column.asc() if ascending else order_column.desc()).limit(limit + 1))).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    ordered = page if ascending else list(reversed(page))
    return RoleRequestPage(requests=[RoleRequestOutput(
        id=request.id, event_id=request.event_id, sequence=request.sequence,
        user_id=request.user_id, sender_name=sender_name,
        role_id=request.role_id, role_name=request.role_name,
        phase_type=request.phase_type, round_number=request.round_number,
        text=request.text, status=request.status, reply_text=request.reply_text,
        linked_event_ids=request.linked_event_ids if is_host else [],
        updated_sequence=request.updated_sequence,
        created_at=request.created_at, updated_at=request.updated_at,
    ) for request, sender_name in ordered],
        next_before_sequence=page[-1][0].sequence if has_more and not ascending else None,
        next_after_sequence=getattr(page[-1][0], "updated_sequence" if after_updated_sequence is not None else "sequence")
        if has_more and ascending else None,
        room_cursor=room.version)


@router.post("", response_model=RoleRequestSendOutput)
async def submit_role_request(room_id: UUID, body: RoleRequestInput,
                              user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_db)) -> RoleRequestSendOutput:
    if await require_room_access(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="Quản trò không gửi yêu cầu role")
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    request = {"user_id": str(user.id), "text": body.text}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "role.request_submitted" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        saved = await db.scalar(select(RoleRequest).where(RoleRequest.event_id == prior.id))
        return RoleRequestSendOutput(id=saved.id, event_id=prior.id, repeated=True)
    role_state = await current_role(db, room_id, user.id)
    reason = block_reason(room, role_state)
    if reason:
        raise HTTPException(status_code=403, detail=reason)
    await check_rate(db, room_id, user.id)
    role = role_state[0]
    event = await append_event(db, room_id, user.id, "role.request_submitted", "host",
                               {"request": request, "role_id": role["id"],
                                "role_name": role["name"], "phase_type": room.phase_type,
                                "round_number": room.round_number},
                               client_request_id=body.client_request_id)
    saved = RoleRequest(room_id=room_id, user_id=user.id, event_id=event.id,
                        sequence=event.sequence, updated_sequence=event.sequence, role_id=UUID(role["id"]),
                        role_name=role["name"], phase_type=room.phase_type,
                        round_number=room.round_number, text=body.text, status="pending")
    db.add(saved)
    await db.flush()
    await db.commit()
    return RoleRequestSendOutput(id=saved.id, event_id=event.id, repeated=False)


@router.get("/{request_id}/linkable-actions", response_model=list[LinkableAction])
async def list_linkable_actions(room_id: UUID, request_id: UUID,
                                user: User = Depends(current_user),
                                db: AsyncSession = Depends(get_db)) -> list[LinkableAction]:
    await require_room_host(db, room_id, user.id)
    saved = await db.get(RoleRequest, request_id)
    if saved is None or saved.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không có yêu cầu này")
    events = (await db.scalars(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.actor_user_id == user.id,
        GameEvent.sequence > saved.sequence, GameEvent.kind.in_(LINKABLE_KINDS)
    ).order_by(GameEvent.sequence.desc()).limit(50))).all()
    target_ids = {UUID(event.payload["request"]["user_id"]) for event in events}
    names = dict((await db.execute(select(User.id, User.display_name).where(User.id.in_(target_ids)))).all()) if target_ids else {}
    return [LinkableAction(event_id=event.id, sequence=event.sequence, kind=event.kind,
                           target_name=names.get(UUID(event.payload["request"]["user_id"]), "Người chơi"),
                           created_at=event.created_at) for event in events]


@router.post("/{request_id}/review", response_model=RoleRequestSendOutput)
async def review_role_request(room_id: UUID, request_id: UUID, body: RoleRequestReviewInput,
                              user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_db)) -> RoleRequestSendOutput:
    await require_room_host(db, room_id, user.id)
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    request = {"user_id": None, "request_id": str(request_id), "status": body.status,
               "reply_text": body.reply_text,
               "linked_event_ids": [str(value) for value in body.linked_event_ids]}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        request["user_id"] = prior.payload.get("request", {}).get("user_id")
        if prior.kind != "role.request_reviewed" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return RoleRequestSendOutput(id=request_id, event_id=prior.id, repeated=True)
    saved = await db.get(RoleRequest, request_id)
    if saved is None or saved.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không có yêu cầu này")
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    if saved.status not in {"pending", "in_review"} or (saved.status == "in_review" and body.status == "in_review"):
        raise HTTPException(status_code=409, detail="Yêu cầu không còn ở trạng thái có thể xử lý")
    if body.linked_event_ids:
        linked = (await db.scalars(select(GameEvent).where(GameEvent.id.in_(body.linked_event_ids)))).all()
        if len(linked) != len(body.linked_event_ids) or any(
            event.room_id != room_id or event.actor_user_id != user.id or
            event.sequence <= saved.sequence or event.kind not in LINKABLE_KINDS for event in linked
        ):
            raise HTTPException(status_code=422, detail="Thao tác liên kết không hợp lệ")
    request["user_id"] = str(saved.user_id)
    event = await append_event(db, room_id, user.id, "role.request_reviewed", "host",
                               {"request": request, "previous_status": saved.status},
                               client_request_id=body.client_request_id)
    saved.status = body.status
    saved.reply_text = body.reply_text
    saved.linked_event_ids = request["linked_event_ids"]
    saved.updated_sequence = event.sequence
    saved.updated_at = now_utc()
    await db.commit()
    return RoleRequestSendOutput(id=saved.id, event_id=event.id, repeated=False)


@router.post("/{request_id}/withdraw", response_model=RoleRequestSendOutput)
async def withdraw_role_request(room_id: UUID, request_id: UUID, body: RoleRequestWithdrawInput,
                                user: User = Depends(current_user),
                                db: AsyncSession = Depends(get_db)) -> RoleRequestSendOutput:
    if await require_room_access(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="Quản trò không thể rút yêu cầu")
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    request = {"user_id": str(user.id), "request_id": str(request_id)}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "role.request_withdrawn" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return RoleRequestSendOutput(id=request_id, event_id=prior.id, repeated=True)
    saved = await db.get(RoleRequest, request_id)
    if saved is None or saved.room_id != room_id or saved.user_id != user.id:
        raise HTTPException(status_code=404, detail="Không có yêu cầu này")
    if room.status != "playing" or saved.status not in {"pending", "in_review"}:
        raise HTTPException(status_code=409, detail="Yêu cầu không còn ở trạng thái có thể rút")
    event = await append_event(db, room_id, user.id, "role.request_withdrawn", "host",
                               {"request": request, "previous_status": saved.status},
                               client_request_id=body.client_request_id)
    saved.status = "withdrawn"
    saved.updated_sequence = event.sequence
    saved.updated_at = now_utc()
    await db.commit()
    return RoleRequestSendOutput(id=saved.id, event_id=event.id, repeated=False)
