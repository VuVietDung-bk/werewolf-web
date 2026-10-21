"""Moderator actions and explicit publication of player status."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access, require_room_host
from app.db.events import append_event
from app.db.models import ContentRevision, GameEvent, PlayerNote, PlayerState, RoleAssignment, RoleRequest, RoleSnapshot, Room, RoomMember, User
from app.db.session import get_db
from app.roles import RoleOutput, effective_role_data
from app.rooms import locked_host_room

router = APIRouter(prefix="/api/rooms/{room_id}/game/players", tags=["game"])
notices_router = APIRouter(prefix="/api/rooms/{room_id}/game", tags=["game"])
ActionKind = Literal["kill", "revive", "add_effect", "remove_effect", "note",
                     "change_role", "change_faction", "change_team"]


class PlayerActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    action: ActionKind
    text: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=1000)
    role_id: UUID | None = None

    @model_validator(mode="after")
    def valid_fields(self):
        self.text = (self.text or "").strip() or None
        self.reason = (self.reason or "").strip() or None
        if self.action != "change_role" and self.role_id is not None:
            raise ValueError("Chỉ lệnh đổi role mới nhận ID role")
        if self.action == "note":
            if not self.text:
                raise ValueError("Ghi chú không được để trống")
            if self.reason is not None:
                raise ValueError("Ghi chú tự chứa lý do; không cần lý do riêng")
        elif self.action in {"add_effect", "remove_effect"}:
            if not self.text or len(self.text) > 80 or not self.reason:
                raise ValueError("Hiệu ứng cần tên tối đa 80 ký tự và lý do")
        elif self.action == "change_role":
            if self.role_id is None or self.text is not None or not self.reason:
                raise ValueError("Đổi role cần chọn role trong ván và ghi lý do")
        elif self.action == "change_faction":
            if not self.text or len(self.text) > 80 or not self.reason:
                raise ValueError("Đổi phe cần tên phe tối đa 80 ký tự và lý do")
        elif self.action == "change_team":
            if (self.text is not None and len(self.text) > 80) or not self.reason:
                raise ValueError("Đổi đội chat cần lý do; để trống tên để bỏ đội")
        elif self.text is not None or not self.reason:
            raise ValueError("Giết hoặc hồi sinh cần lý do và không nhận tên hiệu ứng")
        return self


class PlayerBoardItem(BaseModel):
    user_id: UUID
    display_name: str
    seat_number: int
    role: RoleOutput
    is_alive: bool
    public_is_alive: bool
    publication_disposition: Literal["published", "pending", "held", "skipped"]
    public_role_name: str | None
    effects: list[str]
    note_count: int
    pending_request_count: int


class PlayerNoteOutput(BaseModel):
    id: UUID
    text: str
    created_at: str
    edited_at: datetime | None


class ActionOutput(BaseModel):
    event_id: UUID
    repeated: bool


class AnnouncementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    moderator_reason: str = Field(min_length=1, max_length=1000)
    public_cause: str | None = Field(default=None, max_length=500)
    reveal_role: bool = False

    @model_validator(mode="after")
    def valid_text(self):
        self.moderator_reason = self.moderator_reason.strip()
        self.public_cause = (self.public_cause or "").strip() or None
        if not self.moderator_reason:
            raise ValueError("Cần ghi lý do công bố")
        return self


class PublicationDispositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    decision: Literal["hold", "skip"]
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_reason(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("Cần lý do giữ kín hoặc bỏ qua thông báo")
        return self


class PublicNotice(BaseModel):
    id: UUID
    status: Literal["dead", "alive"]
    user_id: UUID
    display_name: str
    seat_number: int
    public_cause: str | None
    revealed_role_name: str | None
    phase_type: str
    round_number: int
    created_at: datetime
    edited_at: datetime | None


async def host_board(db: AsyncSession, room_id: UUID) -> list[PlayerBoardItem]:
    snapshot = await db.get(RoleSnapshot, room_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Phòng chưa có phân role")
    roles = {role["id"]: role for role in snapshot.definitions}
    counts = select(PlayerNote.user_id, func.count().label("count")).where(
        PlayerNote.room_id == room_id, PlayerNote.deleted_at.is_(None)
    ).group_by(PlayerNote.user_id).subquery()
    request_counts = select(RoleRequest.user_id, func.count().label("count")).where(
        RoleRequest.room_id == room_id, RoleRequest.status.in_(("pending", "in_review"))
    ).group_by(RoleRequest.user_id).subquery()
    rows = (await db.execute(
        select(RoomMember, User, RoleAssignment, PlayerState, counts.c.count,
               request_counts.c.count)
        .join(User, User.id == RoomMember.user_id)
        .join(RoleAssignment, (RoleAssignment.room_id == RoomMember.room_id)
              & (RoleAssignment.user_id == RoomMember.user_id))
        .join(PlayerState, (PlayerState.room_id == RoomMember.room_id)
              & (PlayerState.user_id == RoomMember.user_id))
        .outerjoin(counts, counts.c.user_id == RoomMember.user_id)
        .outerjoin(request_counts, request_counts.c.user_id == RoomMember.user_id)
        .where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
        .order_by(RoomMember.seat_number)
    )).all()
    return [PlayerBoardItem(user_id=member.user_id, display_name=user.display_name,
                            seat_number=member.seat_number,
                            role=RoleOutput.model_validate(effective_role_data(
                                roles, assignment.role_id, state)),
                            is_alive=state.is_alive, public_is_alive=state.public_is_alive,
                            publication_disposition=state.publication_disposition,
                            public_role_name=state.public_role_name, effects=state.effects,
                            note_count=note_count or 0,
                            pending_request_count=pending_request_count or 0)
            for member, user, assignment, state, note_count, pending_request_count in rows]


@router.get("", response_model=list[PlayerBoardItem])
async def get_players(room_id: UUID, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> list[PlayerBoardItem]:
    await require_room_host(db, room_id, user.id)
    room = await db.get(Room, room_id)
    if room.status not in {"playing", "finished"}:
        raise HTTPException(status_code=409, detail="Trận chưa bắt đầu")
    return await host_board(db, room_id)


@router.get("/{player_id}/notes", response_model=list[PlayerNoteOutput])
async def get_notes(room_id: UUID, player_id: UUID, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)) -> list[PlayerNoteOutput]:
    await require_room_host(db, room_id, user.id)
    if await db.get(PlayerState, (room_id, player_id)) is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    notes = (await db.scalars(select(PlayerNote).where(
        PlayerNote.room_id == room_id, PlayerNote.user_id == player_id,
        PlayerNote.deleted_at.is_(None)
    ).order_by(PlayerNote.created_at.desc(), PlayerNote.id.desc()).limit(30))).all()
    return [PlayerNoteOutput(id=note.id, text=note.current_text if note.edited_at else note.text,
                             created_at=note.created_at.isoformat(), edited_at=note.edited_at)
            for note in notes]


@router.post("/{player_id}/actions", response_model=ActionOutput)
async def player_action(room_id: UUID, player_id: UUID, body: PlayerActionInput,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> ActionOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"user_id": str(player_id), "action": body.action,
               "text": body.text, "reason": body.reason}
    if body.role_id is not None:
        request["role_id"] = str(body.role_id)
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != f"player.{body.action}" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ActionOutput(event_id=prior.id, repeated=True)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    state = await db.get(PlayerState, (room_id, player_id))
    if state is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    if room.is_paused and body.action != "note":
        raise HTTPException(status_code=409, detail="Trận đang tạm dừng")
    previous: dict = {"is_alive": state.is_alive, "effects": list(state.effects),
                      "publication_disposition": state.publication_disposition}
    role_snapshot = None
    assignment = None
    definitions = None
    if body.action in {"change_role", "change_faction", "change_team"}:
        role_snapshot = await db.get(RoleSnapshot, room_id)
        assignment = await db.get(RoleAssignment, (room_id, player_id))
        definitions = {role["id"]: role for role in role_snapshot.definitions}
        before = effective_role_data(definitions, assignment.role_id, state)
        previous["role_id"] = before["id"]
        previous["faction_label"] = before["faction_label"]
        previous["team_chat_label"] = before["team_chat_label"]
    if body.action == "kill":
        if not state.is_alive:
            raise HTTPException(status_code=409, detail="Người chơi đã chết")
        state.is_alive = False
    elif body.action == "revive":
        if state.is_alive:
            raise HTTPException(status_code=409, detail="Người chơi đang sống")
        state.is_alive = True
    elif body.action == "add_effect":
        if any(value.casefold() == body.text.casefold() for value in state.effects):
            raise HTTPException(status_code=409, detail="Hiệu ứng đã tồn tại")
        state.effects = [*state.effects, body.text]
    elif body.action == "remove_effect":
        if not any(value.casefold() == body.text.casefold() for value in state.effects):
            raise HTTPException(status_code=409, detail="Không có hiệu ứng này")
        state.effects = [value for value in state.effects if value.casefold() != body.text.casefold()]
    elif body.action == "change_role":
        if str(body.role_id) not in definitions:
            raise HTTPException(status_code=422, detail="Role mới không thuộc bản lưu trận")
        if (str(body.role_id) == before["id"] and state.faction_label_override is None
                and not state.team_chat_override_set):
            raise HTTPException(status_code=409, detail="Người chơi đã có role này")
        state.current_role_id = body.role_id
        state.faction_label_override = None
        state.team_chat_override_set = False
        state.team_chat_label_override = None
    elif body.action == "change_faction":
        if body.text == before["faction_label"]:
            raise HTTPException(status_code=409, detail="Người chơi đã ở phe này")
        state.faction_label_override = body.text
    elif body.action == "change_team":
        if body.text == before["team_chat_label"]:
            raise HTTPException(status_code=409, detail="Người chơi đã ở đội chat này")
        state.team_chat_override_set = True
        state.team_chat_label_override = body.text
    else:
        db.add(PlayerNote(room_id=room_id, user_id=player_id, text=body.text))
    if body.action in {"kill", "revive"}:
        state.publication_disposition = ("pending" if state.is_alive != state.public_is_alive
                                         else "published")
    current: dict = {"is_alive": state.is_alive, "effects": list(state.effects),
                     "publication_disposition": state.publication_disposition}
    if definitions is not None:
        after = effective_role_data(definitions, assignment.role_id, state)
        current.update({"role_id": after["id"], "faction_label": after["faction_label"],
                        "team_chat_label": after["team_chat_label"]})
    event = await append_event(db, room_id, user.id, f"player.{body.action}", "host",
                               {"request": request, "previous": previous,
                                "current": current},
                               client_request_id=body.client_request_id)
    await db.commit()
    return ActionOutput(event_id=event.id, repeated=False)


@router.post("/{player_id}/publication-disposition", response_model=ActionOutput)
async def decide_publication_disposition(room_id: UUID, player_id: UUID,
                                         body: PublicationDispositionInput,
                                         user: User = Depends(current_user),
                                         db: AsyncSession = Depends(get_db)) -> ActionOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"user_id": str(player_id), "decision": body.decision, "reason": body.reason}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "player.publication_disposition_decided" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ActionOutput(event_id=prior.id, repeated=True)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    state = await db.get(PlayerState, (room_id, player_id))
    if state is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    if state.is_alive == state.public_is_alive:
        raise HTTPException(status_code=409, detail="Không có trạng thái sống/chết cần xử lý")
    disposition = "held" if body.decision == "hold" else "skipped"
    if state.publication_disposition == disposition:
        raise HTTPException(status_code=409, detail="Đã ghi cùng quyết định cho trạng thái này")
    status_event = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.kind.in_(("player.kill", "player.revive")),
        GameEvent.payload["request"]["user_id"].astext == str(player_id)
    ).order_by(GameEvent.sequence.desc()).limit(1))
    previous = state.publication_disposition
    state.publication_disposition = disposition
    event = await append_event(db, room_id, user.id, "player.publication_disposition_decided", "host",
                               {"request": request, "previous_disposition": previous,
                                "new_disposition": disposition,
                                "is_alive": state.is_alive,
                                "public_is_alive": state.public_is_alive,
                                "status_event_id": str(status_event.id) if status_event else None,
                                "phase_type": room.phase_type, "round_number": room.round_number},
                               client_request_id=body.client_request_id)
    await db.commit()
    return ActionOutput(event_id=event.id, repeated=False)


@router.post("/{player_id}/announce", response_model=ActionOutput)
async def announce_player_status(room_id: UUID, player_id: UUID, body: AnnouncementInput,
                                 user: User = Depends(current_user),
                                 db: AsyncSession = Depends(get_db)) -> ActionOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"user_id": str(player_id), "moderator_reason": body.moderator_reason,
               "public_cause": body.public_cause, "reveal_role": body.reveal_role}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "player.announcement_decided" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ActionOutput(event_id=prior.id, repeated=True)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    state = await db.get(PlayerState, (room_id, player_id))
    if state is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    if state.public_is_alive == state.is_alive:
        raise HTTPException(status_code=409, detail="Không có thay đổi sống/chết chờ công bố")
    member = await db.get(RoomMember, (room_id, player_id))
    player = await db.get(User, player_id)
    if member is None or player is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    revealed_role_name = None
    if body.reveal_role:
        assignment = await db.get(RoleAssignment, (room_id, player_id))
        snapshot = await db.get(RoleSnapshot, room_id)
        effective_id = str(state.current_role_id or assignment.role_id)
        revealed_role_name = next((role["name"] for role in snapshot.definitions
                                   if role["id"] == effective_id), None)
        if revealed_role_name is None:
            raise HTTPException(status_code=409, detail="Không tìm thấy role trong bản lưu trận")
    previous_public_is_alive = state.public_is_alive
    previous_disposition = state.publication_disposition
    state.public_is_alive = state.is_alive
    state.publication_disposition = "published"
    if revealed_role_name is not None:
        state.public_role_name = revealed_role_name
    decision = await append_event(db, room_id, user.id, "player.announcement_decided", "host",
                                  {"request": request,
                                   "previous_public_is_alive": previous_public_is_alive,
                                   "new_public_is_alive": state.public_is_alive,
                                   "previous_disposition": previous_disposition,
                                   "new_disposition": state.publication_disposition},
                                  client_request_id=body.client_request_id)
    await append_event(db, room_id, user.id,
                       "player.revival_announced" if state.is_alive else "player.death_announced",
                       "room", {"user_id": str(player_id), "display_name": player.display_name,
                                "seat_number": member.seat_number,
                                "status": "alive" if state.is_alive else "dead",
                                "public_cause": body.public_cause,
                                "revealed_role_name": revealed_role_name,
                                "phase_type": room.phase_type,
                                "round_number": room.round_number})
    await db.commit()
    return ActionOutput(event_id=decision.id, repeated=False)


@notices_router.get("/death-notices", response_model=list[PublicNotice])
async def get_death_notices(room_id: UUID, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_db)) -> list[PublicNotice]:
    await require_room_access(db, room_id, user.id)
    events = (await db.scalars(select(GameEvent).where(
        GameEvent.room_id == room_id,
        GameEvent.visibility == "room",
        GameEvent.kind.in_(("player.death_announced", "player.revival_announced")),
    ).order_by(GameEvent.sequence))).all()
    revisions = {row.source_event_id: row for row in (await db.scalars(
        select(ContentRevision).where(ContentRevision.room_id == room_id,
                                      ContentRevision.source_event_id.in_([event.id for event in events]))
    )).all()} if events else {}
    return [PublicNotice(id=event.id, status=event.payload["status"],
                         user_id=UUID(event.payload["user_id"]),
                         display_name=event.payload["display_name"],
                         seat_number=event.payload["seat_number"],
                         public_cause=(revisions[event.id].current_text
                                       if event.id in revisions and revisions[event.id].edited_at
                                       else event.payload["public_cause"]),
                         revealed_role_name=event.payload["revealed_role_name"],
                         phase_type=event.payload["phase_type"],
                         round_number=event.payload["round_number"],
                         created_at=event.created_at,
                         edited_at=revisions[event.id].edited_at if event.id in revisions else None)
            for event in events if event.id not in revisions or revisions[event.id].deleted_at is None]
