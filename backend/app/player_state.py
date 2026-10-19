"""Private moderator board and manual player actions. No player state enters room output."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_host
from app.db.events import append_event
from app.db.models import GameEvent, PlayerNote, PlayerState, RoleAssignment, RoleSnapshot, Room, RoomMember, User
from app.db.session import get_db
from app.roles import RoleOutput, snapshot_role_data
from app.rooms import locked_playing_room

router = APIRouter(prefix="/api/rooms/{room_id}/game/players", tags=["game"])
ActionKind = Literal["kill", "revive", "add_effect", "remove_effect", "note"]


class PlayerActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    action: ActionKind
    text: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def valid_fields(self):
        self.text = (self.text or "").strip() or None
        self.reason = (self.reason or "").strip() or None
        if self.action == "note":
            if not self.text:
                raise ValueError("Ghi chú không được để trống")
            if self.reason is not None:
                raise ValueError("Ghi chú tự chứa lý do; không cần lý do riêng")
        elif self.action in {"add_effect", "remove_effect"}:
            if not self.text or len(self.text) > 80 or not self.reason:
                raise ValueError("Hiệu ứng cần tên tối đa 80 ký tự và lý do")
        elif self.text is not None or not self.reason:
            raise ValueError("Giết hoặc hồi sinh cần lý do và không nhận tên hiệu ứng")
        return self


class PlayerBoardItem(BaseModel):
    user_id: UUID
    display_name: str
    seat_number: int
    role: RoleOutput
    is_alive: bool
    effects: list[str]
    note_count: int


class PlayerNoteOutput(BaseModel):
    id: UUID
    text: str
    created_at: str


class ActionOutput(BaseModel):
    event_id: UUID
    repeated: bool


async def host_board(db: AsyncSession, room_id: UUID) -> list[PlayerBoardItem]:
    snapshot = await db.get(RoleSnapshot, room_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Phòng chưa có phân role")
    roles = {role["id"]: RoleOutput.model_validate(snapshot_role_data(role))
             for role in snapshot.definitions}
    counts = select(PlayerNote.user_id, func.count().label("count")).where(
        PlayerNote.room_id == room_id).group_by(PlayerNote.user_id).subquery()
    rows = (await db.execute(
        select(RoomMember, User, RoleAssignment, PlayerState, counts.c.count)
        .join(User, User.id == RoomMember.user_id)
        .join(RoleAssignment, (RoleAssignment.room_id == RoomMember.room_id)
              & (RoleAssignment.user_id == RoomMember.user_id))
        .join(PlayerState, (PlayerState.room_id == RoomMember.room_id)
              & (PlayerState.user_id == RoomMember.user_id))
        .outerjoin(counts, counts.c.user_id == RoomMember.user_id)
        .where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
        .order_by(RoomMember.seat_number)
    )).all()
    return [PlayerBoardItem(user_id=member.user_id, display_name=user.display_name,
                            seat_number=member.seat_number, role=roles[str(assignment.role_id)],
                            is_alive=state.is_alive, effects=state.effects,
                            note_count=note_count or 0)
            for member, user, assignment, state, note_count in rows]


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
        PlayerNote.room_id == room_id, PlayerNote.user_id == player_id
    ).order_by(PlayerNote.created_at.desc(), PlayerNote.id.desc()).limit(30))).all()
    return [PlayerNoteOutput(id=note.id, text=note.text,
                             created_at=note.created_at.isoformat()) for note in notes]


@router.post("/{player_id}/actions", response_model=ActionOutput)
async def player_action(room_id: UUID, player_id: UUID, body: PlayerActionInput,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> ActionOutput:
    room = await locked_playing_room(db, room_id, user.id)
    request = {"user_id": str(player_id), "action": body.action,
               "text": body.text, "reason": body.reason}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != f"player.{body.action}" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return ActionOutput(event_id=prior.id, repeated=True)
    state = await db.get(PlayerState, (room_id, player_id))
    if state is None:
        raise HTTPException(status_code=404, detail="Người chơi không thuộc trận")
    if room.is_paused and body.action != "note":
        raise HTTPException(status_code=409, detail="Trận đang tạm dừng")
    previous: dict = {"is_alive": state.is_alive, "effects": list(state.effects)}
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
    else:
        db.add(PlayerNote(room_id=room_id, user_id=player_id, text=body.text))
    event = await append_event(db, room_id, user.id, f"player.{body.action}", "host",
                               {"request": request, "previous": previous,
                                "current": {"is_alive": state.is_alive,
                                            "effects": list(state.effects)}},
                               client_request_id=body.client_request_id)
    await db.commit()
    return ActionOutput(event_id=event.id, repeated=False)
