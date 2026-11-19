"""Moderator-owned lobbies. All seat changes serialize on the room row."""

import secrets
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import POSTGAME_POLICY_VERSION, current_user, now_utc, require_room_access
from app.db.events import append_event
from app.db.models import DraftTurn, GameEvent, PlayerState, RoleAssignment, RoleDefinition, RoleIcon, RoleSet, RoleSnapshot, Room, RoomMember, User, VoteRound
from app.db.session import get_db
from app.scheduled_announcements import cancel_all_scheduled, cancel_status_schedule, release_scheduled

router = APIRouter(prefix="/api/rooms", tags=["rooms"])
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class CreateRoomInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    seat_limit: int = Field(ge=2, le=30)


class JoinRoomInput(BaseModel):
    code: str = Field(min_length=8, max_length=16)
    accept_postgame_transcript: Literal[True]


class RoomSettingsInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    seat_limit: int = Field(ge=2, le=30)
    registration_open: bool


class AdvancePhaseInput(BaseModel):
    expected_phase_type: Literal["day", "night"]
    expected_round_number: int = Field(ge=0)
    client_request_id: UUID | None = None


class PauseInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    client_request_id: UUID | None = None


class GameCommandInput(BaseModel):
    client_request_id: UUID | None = None


class FinishInput(BaseModel):
    result_label: str = Field(min_length=1, max_length=120)
    result_reason: str = Field(min_length=1, max_length=2000)
    client_request_id: UUID | None = None


class KickInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    client_request_id: UUID | None = None


class MemberOutput(BaseModel):
    user_id: UUID
    display_name: str
    seat_number: int
    joined_at: datetime
    public_is_alive: bool
    public_role_name: str | None


class RoomSummary(BaseModel):
    id: UUID
    code: str
    name: str
    seat_limit: int
    status: str
    phase_type: str | None
    round_number: int
    is_paused: bool
    result_label: str | None
    result_reason: str | None
    ended_at: datetime | None
    next_room_id: UUID | None
    is_host: bool


class RoomOutput(RoomSummary):
    host_id: UUID
    host_name: str
    version: int
    members: list[MemberOutput]


def room_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))


async def lock_account(db: AsyncSession, user_id: UUID) -> None:
    """Serialize room entry for one account across API processes."""
    key = int.from_bytes(user_id.bytes[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(key)))


async def active_room_id(db: AsyncSession, user_id: UUID,
                         except_room_id: UUID | None = None) -> UUID | None:
    statement = select(Room.id).outerjoin(
        RoomMember, (RoomMember.room_id == Room.id) & (RoomMember.user_id == user_id)
    ).where(Room.status.not_in(("finished", "cancelled")), or_(
        Room.owner_user_id == user_id, RoomMember.left_at.is_(None) &
        (RoomMember.user_id == user_id)
    ))
    if except_room_id is not None:
        statement = statement.where(Room.id != except_room_id)
    return await db.scalar(statement.limit(1))


async def room_output(db: AsyncSession, room: Room, user_id: UUID) -> RoomOutput:
    host = await db.get(User, room.owner_user_id)
    rows = (await db.execute(
        select(RoomMember, User, PlayerState)
        .join(User, User.id == RoomMember.user_id)
        .outerjoin(PlayerState, (PlayerState.room_id == RoomMember.room_id)
                   & (PlayerState.user_id == RoomMember.user_id))
        .where(RoomMember.room_id == room.id, RoomMember.left_at.is_(None))
        .order_by(RoomMember.seat_number)
    )).all()
    return RoomOutput(
        id=room.id, code=room.code, name=room.name, seat_limit=room.seat_limit,
        status=room.status, phase_type=room.phase_type, round_number=room.round_number,
        is_paused=room.is_paused, result_label=room.result_label,
        result_reason=room.result_reason, ended_at=room.ended_at,
        next_room_id=room.next_room_id,
        is_host=room.owner_user_id == user_id,
        host_id=host.id, host_name=host.display_name,
        version=room.version if room.owner_user_id == user_id else room.public_version,
        members=[MemberOutput(user_id=member.user_id, display_name=person.display_name,
                              seat_number=member.seat_number, joined_at=member.joined_at,
                              public_is_alive=state.public_is_alive if state else True,
                              public_role_name=state.public_role_name if state else None)
                 for member, person, state in rows],
    )


@router.post("", response_model=RoomOutput, status_code=201)
async def create_room(
    body: CreateRoomInput, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> RoomOutput:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Tên phòng không được để trống")
    await lock_account(db, user.id)
    if await active_room_id(db, user.id) is not None:
        raise HTTPException(status_code=409, detail="Bạn đang ở một phòng hoạt động; hãy rời hoặc kết thúc phòng đó trước")
    room = Room(owner_user_id=user.id, code=room_code(), name=name, seat_limit=body.seat_limit)
    db.add(room)
    await db.flush()
    db.add(RoleSet(room_id=room.id))
    await append_event(db, room.id, user.id, "room.created", "room",
                       {"name": name, "seat_limit": body.seat_limit})
    await db.commit()
    return await room_output(db, room, user.id)


@router.get("/mine", response_model=list[RoomSummary])
async def my_rooms(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[RoomSummary]:
    active = select(RoomMember.room_id).join(Room, Room.id == RoomMember.room_id).where(
        RoomMember.user_id == user.id,
        RoomMember.consent_version == POSTGAME_POLICY_VERSION,
        or_(RoomMember.left_at.is_(None), Room.status == "finished"),
    )
    rooms = (await db.scalars(
        select(Room).where(Room.status != "cancelled",
                           or_(Room.owner_user_id == user.id, Room.id.in_(active)))
        .order_by(Room.created_at.desc())
    )).all()
    return [RoomSummary(id=room.id, code=room.code, name=room.name,
                        seat_limit=room.seat_limit, status=room.status,
                        phase_type=room.phase_type, round_number=room.round_number,
                        is_paused=room.is_paused, result_label=room.result_label,
                        result_reason=room.result_reason, ended_at=room.ended_at,
                        next_room_id=room.next_room_id,
                        is_host=room.owner_user_id == user.id) for room in rooms]


@router.post("/join", response_model=RoomOutput)
async def join_room(
    body: JoinRoomInput, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> RoomOutput:
    code = body.code.strip().upper()
    await lock_account(db, user.id)
    room = (await db.scalars(select(Room).where(Room.code == code).with_for_update())).one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Mã phòng không đúng")
    if room.owner_user_id == user.id:
        raise HTTPException(status_code=403, detail="Quản trò không chiếm ghế người chơi")
    member = await db.get(RoomMember, (room.id, user.id))
    if member is not None and member.left_at is None:
        if member.consent_version != POSTGAME_POLICY_VERSION:
            member.consent_version = POSTGAME_POLICY_VERSION
            member.consented_at = now_utc()
            await append_event(db, room.id, user.id, "member.reconsented", "room",
                               {"user_id": str(user.id), "policy": POSTGAME_POLICY_VERSION})
            await db.commit()
        return await room_output(db, room, user.id)
    if room.status != "open":
        raise HTTPException(status_code=409, detail="Phòng đã đóng đăng ký")
    if await active_room_id(db, user.id, room.id) is not None:
        raise HTTPException(status_code=409, detail="Bạn đang ở một phòng hoạt động; hãy rời hoặc kết thúc phòng đó trước")
    occupied = set((await db.scalars(select(RoomMember.seat_number).where(
        RoomMember.room_id == room.id, RoomMember.left_at.is_(None)
    ))).all())
    seat = next((number for number in range(1, room.seat_limit + 1) if number not in occupied), None)
    if seat is None:
        raise HTTPException(status_code=409, detail="Phòng đã đủ người")
    if member is None:
        member = RoomMember(room_id=room.id, user_id=user.id, seat_number=seat,
                            consent_version=POSTGAME_POLICY_VERSION, consented_at=now_utc())
        db.add(member)
    else:
        member.seat_number = seat
        member.left_at = None
        member.joined_at = now_utc()
        member.consented_at = now_utc()
        member.consent_version = POSTGAME_POLICY_VERSION
    await db.flush()
    await append_event(db, room.id, user.id, "member.joined", "room",
                       {"user_id": str(user.id), "seat_number": seat,
                        "policy": POSTGAME_POLICY_VERSION})
    await db.commit()
    return await room_output(db, room, user.id)


@router.get("/{room_id}", response_model=RoomOutput)
async def get_room(
    room_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> RoomOutput:
    await require_room_access(db, room_id, user.id)
    return await room_output(db, await db.get(Room, room_id), user.id)


@router.patch("/{room_id}/settings", response_model=RoomOutput)
async def update_settings(
    room_id: UUID, body: RoomSettingsInput,
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> RoomOutput:
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")
    if room.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Chỉ quản trò được thay đổi phòng")
    if room.status not in {"open", "closed"}:
        raise HTTPException(status_code=409, detail="Trận đã bắt đầu")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Tên phòng không được để trống")
    highest_seat = max((await db.scalars(select(RoomMember.seat_number).where(
        RoomMember.room_id == room.id, RoomMember.left_at.is_(None)
    ))).all(), default=0)
    if body.seat_limit < highest_seat:
        raise HTTPException(status_code=409, detail="Giới hạn thấp hơn số ghế đang dùng")
    room.name = name
    room.seat_limit = body.seat_limit
    room.status = "open" if body.registration_open else "closed"
    await append_event(db, room.id, user.id, "room.settings_updated", "room",
                       {"name": name, "seat_limit": body.seat_limit,
                        "registration_open": body.registration_open})
    await db.commit()
    return await room_output(db, room, user.id)


@router.post("/{room_id}/leave", status_code=204)
async def leave_room(
    room_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await lock_account(db, user.id)
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")
    if room.owner_user_id == user.id:
        raise HTTPException(status_code=403, detail="Quản trò không có ghế để rời")
    if room.status not in {"open", "closed", "finished"}:
        raise HTTPException(status_code=409, detail="Không thể rời sau khi trận bắt đầu")
    member = await db.get(RoomMember, (room_id, user.id))
    if member is None or member.left_at is not None:
        raise HTTPException(status_code=403, detail="Bạn không ở trong phòng")
    member.left_at = now_utc()
    if room.status == "playing":
        await cancel_status_schedule(db, room_id, player_id, user.id,
                                     "Người chơi rời phòng trước khi công bố")
    await append_event(db, room.id, user.id, "member.left", "room",
                       {"user_id": str(user.id), "seat_number": member.seat_number})
    await db.commit()


@router.post("/{room_id}/players/{player_id}/kick", response_model=RoomOutput)
async def kick_player(room_id: UUID, player_id: UUID, body: KickInput,
                      user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> RoomOutput:
    if player_id == user.id:
        raise HTTPException(status_code=403, detail="Quản trò không thể tự kick")
    await lock_account(db, player_id)
    room = await locked_host_room(db, room_id, user.id)
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Cần nhập lý do kick")
    request = {"user_id": str(player_id), "reason": reason}
    if await repeated_game_command(db, room_id, "member.kicked", body.client_request_id, request):
        return await room_output(db, room, user.id)
    if room.status not in {"open", "closed", "drafting", "assigned", "playing", "finished"}:
        raise HTTPException(status_code=409, detail="Phòng không nhận thao tác kick")
    if room.status == "finished" and room.next_room_id is not None:
        raise HTTPException(status_code=409, detail="Ván mới đã mở; hãy kick ở sảnh ván mới")
    if room.status == "playing":
        from app.votes import close_expired_vote
        if await close_expired_vote(db, room_id):
            room = await locked_host_room(db, room_id, user.id)
    if room.status == "playing" and await db.scalar(select(VoteRound.id).where(
        VoteRound.room_id == room_id, VoteRound.status == "open")) is not None:
        raise HTTPException(status_code=409, detail="Đóng vòng vote đang mở trước khi kick")
    member = await db.get(RoomMember, (room_id, player_id))
    if member is None or member.left_at is not None:
        raise HTTPException(status_code=404, detail="Người chơi không còn ở phòng")
    member.left_at = now_utc()
    if room.status in {"drafting", "assigned"}:
        assignment = await db.get(RoleAssignment, (room_id, player_id))
        if assignment is not None:
            await db.delete(assignment)
    removed_current_turn = False
    if room.status == "drafting":
        from app.draft import all_turns, choose_offers

        turns = await all_turns(db, room_id)
        current = next((turn for turn in turns if turn.chosen_role_id is None), None)
        removed_current_turn = current is not None and current.user_id == player_id
        turn = next((item for item in turns if item.user_id == player_id), None)
        if turn is None:
            raise HTTPException(status_code=409, detail="Không tìm thấy lượt chọn role của người chơi")
        await db.delete(turn)
        await db.flush()
    await append_event(db, room_id, user.id, "member.kicked", "room",
                       {"request": request, "seat_number": member.seat_number,
                        "phase_type": room.phase_type, "round_number": room.round_number},
                       client_request_id=body.client_request_id)
    if room.status == "drafting" and removed_current_turn:
        turns = await all_turns(db, room_id)
        next_turn = next((turn for turn in turns if turn.chosen_role_id is None), None)
        if next_turn is None:
            room.status = "assigned"
            await append_event(db, room_id, user.id, "roles.draft_completed", "room",
                               {"total": len(turns)})
        else:
            snapshot = await db.get(RoleSnapshot, room_id)
            next_turn.offers = choose_offers(snapshot, turns)
            next_turn.offered_at = now_utc()
            await append_event(db, room_id, user.id, "roles.draft_offered", "host",
                               {"turn_index": next_turn.turn_index, "user_id": str(next_turn.user_id),
                                "offers": next_turn.offers})
    await db.commit()
    return await room_output(db, room, user.id)


@router.post("/{room_id}/cancel", status_code=204)
async def cancel_lobby(room_id: UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)) -> None:
    await lock_account(db, user.id)
    room = await locked_host_room(db, room_id, user.id)
    if room.status not in {"open", "closed", "drafting", "assigned"}:
        raise HTTPException(status_code=409, detail="Chỉ có thể đóng sảnh trước khi bắt đầu trận")
    room.status = "cancelled"
    room.ended_at = now_utc()
    await append_event(db, room_id, user.id, "room.cancelled", "room", {})
    await db.commit()


@router.post("/{room_id}/game/next", response_model=RoomOutput)
async def next_game(room_id: UUID, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)) -> RoomOutput:
    # Lock account rows before the room row; join/leave follow the same order.
    candidate_ids = (await db.scalars(select(RoomMember.user_id).where(
        RoomMember.room_id == room_id, RoomMember.left_at.is_(None)))).all()
    for account_id in sorted({user.id, *candidate_ids}):
        await lock_account(db, account_id)
    room = await locked_host_room(db, room_id, user.id)
    if room.status != "finished":
        raise HTTPException(status_code=409, detail="Cần kết thúc trận trước khi mở ván mới")
    if room.next_room_id is not None:
        return await room_output(db, await db.get(Room, room.next_room_id), user.id)
    if await active_room_id(db, user.id) is not None:
        raise HTTPException(status_code=409, detail="Bạn đang quản trò hoặc chơi ở phòng khác; hãy rời phòng đó trước")
    members = (await db.scalars(select(RoomMember).where(
        RoomMember.room_id == room_id, RoomMember.left_at.is_(None)
    ).order_by(RoomMember.seat_number))).all()
    if {member.user_id for member in members} != set(candidate_ids):
        raise HTTPException(status_code=409, detail="Danh sách người chơi vừa thay đổi; hãy thử lại")
    carried = [member for member in members if await active_room_id(db, member.user_id) is None]
    old_code = room.code
    while True:
        archived_code = room_code()
        if archived_code != old_code and await db.scalar(select(Room.id).where(Room.code == archived_code)) is None:
            break
    room.code = archived_code
    await db.flush()
    successor = Room(owner_user_id=user.id, code=old_code, name=room.name,
                     seat_limit=room.seat_limit)
    db.add(successor)
    await db.flush()
    room.next_room_id = successor.id
    role_set = await db.get(RoleSet, room_id)
    db.add(RoleSet(room_id=successor.id,
                   name=role_set.name if role_set else "Bộ role của phòng"))
    roles = (await db.scalars(select(RoleDefinition).where(RoleDefinition.room_id == room_id))).all()
    used_icon_ids = {role.icon_id for role in roles if role.icon_id is not None}
    icons = (await db.scalars(select(RoleIcon).where(
        RoleIcon.room_id == room_id, RoleIcon.id.in_(used_icon_ids)))).all() if used_icon_ids else []
    icon_ids: dict[UUID, UUID] = {}
    for icon in icons:
        copy = RoleIcon(room_id=successor.id, image_bytes=icon.image_bytes,
                        width=icon.width, height=icon.height)
        db.add(copy)
        await db.flush()
        icon_ids[icon.id] = copy.id
    for role in roles:
        db.add(RoleDefinition(room_id=successor.id, name=role.name,
            description=role.description, faction_label=role.faction_label,
            seer_result=role.seer_result, team_chat_label=role.team_chat_label,
            quantity=role.quantity, role_kind=role.role_kind,
            request_phases=role.request_phases, request_when_dead=role.request_when_dead,
            host_contact=role.host_contact, icon_key=role.icon_key,
            icon_id=icon_ids.get(role.icon_id)))
    for member in carried:
        db.add(RoomMember(room_id=successor.id, user_id=member.user_id,
            seat_number=member.seat_number, consent_version=member.consent_version,
            consented_at=member.consented_at))
    await append_event(db, successor.id, user.id, "room.created", "room",
                       {"name": successor.name, "seat_limit": successor.seat_limit,
                        "previous_room_id": str(room_id),
                        "carried_user_ids": [str(member.user_id) for member in carried]})
    await append_event(db, room_id, user.id, "room.next_game_opened", "room",
                       {"next_room_id": str(successor.id)})
    await db.commit()
    return await room_output(db, successor, user.id)


@router.post("/{room_id}/game/start", response_model=RoomOutput)
async def start_game(
    room_id: UUID, body: GameCommandInput | None = None,
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> RoomOutput:
    room = await locked_host_room(db, room_id, user.id)
    request_id = body.client_request_id if body else None
    if await repeated_game_command(db, room_id, "game.started", request_id, {}):
        return await room_output(db, room, user.id)
    if room.status != "assigned":
        raise HTTPException(status_code=409, detail="Cần phát role đầy đủ trước khi bắt đầu ngày 0")
    snapshot = await db.get(RoleSnapshot, room_id)
    members = set((await db.scalars(select(RoomMember.user_id).where(
        RoomMember.room_id == room_id, RoomMember.left_at.is_(None)))).all())
    assigned = set((await db.scalars(select(RoleAssignment.user_id).where(
        RoleAssignment.room_id == room_id))).all())
    if snapshot is None or len(members) < 2 or assigned != members:
        raise HTTPException(status_code=409, detail="Mỗi người chơi cần có đúng một role")
    room.status = "playing"
    room.phase_type = "day"
    room.round_number = 0
    db.add_all(PlayerState(room_id=room_id, user_id=member_id) for member_id in members)
    await append_event(db, room_id, user.id, "game.started", "room",
                       {"phase_type": "day", "round_number": 0, "request": {}},
                       client_request_id=request_id)
    await db.commit()
    return await room_output(db, room, user.id)


async def locked_host_room(db: AsyncSession, room_id: UUID, user_id: UUID) -> Room:
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")
    if room.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Chỉ quản trò được điều khiển trận")
    return room


async def repeated_game_command(db: AsyncSession, room_id: UUID, kind: str,
                                request_id: UUID | None, request: dict) -> bool:
    if request_id is None:
        return False
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == request_id))
    if prior is None:
        return False
    if prior.kind != kind or prior.payload.get("request") != request:
        raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
    return True


@router.post("/{room_id}/game/advance", response_model=RoomOutput)
async def advance_phase(room_id: UUID, body: AdvancePhaseInput,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> RoomOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"expected_phase_type": body.expected_phase_type,
               "expected_round_number": body.expected_round_number}
    if await repeated_game_command(db, room_id, "game.phase_advanced",
                                   body.client_request_id, request):
        return await room_output(db, room, user.id)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    if room.is_paused:
        raise HTTPException(status_code=409, detail="Tiếp tục trận trước khi chuyển pha")
    if (room.phase_type, room.round_number) != (body.expected_phase_type, body.expected_round_number):
        raise HTTPException(status_code=409, detail="Pha đã thay đổi; tải lại phòng trước khi tiếp tục")
    from app.votes import close_expired_vote
    if await close_expired_vote(db, room_id):
        room = await locked_host_room(db, room_id, user.id)
    if await db.scalar(select(VoteRound.id).where(
        VoteRound.room_id == room_id, VoteRound.status == "open")) is not None:
        raise HTTPException(status_code=409, detail="Đóng vòng vote đang mở trước khi chuyển pha")
    previous = {"phase_type": room.phase_type, "round_number": room.round_number}
    if room.phase_type == "day":
        room.phase_type = "night"
        room.round_number += 1
    else:
        room.phase_type = "day"
    await append_event(db, room_id, user.id, "game.phase_advanced", "room",
                       {"previous": previous, "current": {
                           "phase_type": room.phase_type, "round_number": room.round_number},
                        "request": request}, client_request_id=body.client_request_id)
    await release_scheduled(db, room, user.id)
    await db.commit()
    return await room_output(db, room, user.id)


@router.post("/{room_id}/game/pause", response_model=RoomOutput)
async def pause_game(room_id: UUID, body: PauseInput,
                     user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)) -> RoomOutput:
    room = await locked_host_room(db, room_id, user.id)
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Cần ghi lý do tạm dừng")
    if await repeated_game_command(db, room_id, "game.paused",
                                   body.client_request_id, {"reason": reason}):
        return await room_output(db, room, user.id)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    if room.is_paused:
        raise HTTPException(status_code=409, detail="Trận đã tạm dừng")
    room.is_paused = True
    await append_event(db, room_id, user.id, "game.paused", "room",
                       {"reason": reason, "phase_type": room.phase_type,
                        "round_number": room.round_number, "request": {"reason": reason}},
                       client_request_id=body.client_request_id)
    await db.commit()
    return await room_output(db, room, user.id)


@router.post("/{room_id}/game/resume", response_model=RoomOutput)
async def resume_game(room_id: UUID, body: GameCommandInput | None = None,
                      user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> RoomOutput:
    room = await locked_host_room(db, room_id, user.id)
    request_id = body.client_request_id if body else None
    if await repeated_game_command(db, room_id, "game.resumed", request_id, {}):
        return await room_output(db, room, user.id)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    if not room.is_paused:
        raise HTTPException(status_code=409, detail="Trận chưa tạm dừng")
    room.is_paused = False
    await append_event(db, room_id, user.id, "game.resumed", "room",
                       {"phase_type": room.phase_type, "round_number": room.round_number,
                        "request": {}}, client_request_id=request_id)
    await db.commit()
    return await room_output(db, room, user.id)


@router.post("/{room_id}/game/finish", response_model=RoomOutput)
async def finish_game(room_id: UUID, body: FinishInput,
                      user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> RoomOutput:
    room = await locked_host_room(db, room_id, user.id)
    result, reason = body.result_label.strip(), body.result_reason.strip()
    if not result or not reason:
        raise HTTPException(status_code=422, detail="Cần nhập kết quả và giải thích")
    request = {"result_label": result, "result_reason": reason}
    if await repeated_game_command(db, room_id, "game.finished",
                                   body.client_request_id, request):
        return await room_output(db, room, user.id)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Trận không ở trạng thái đang chơi")
    from app.votes import close_expired_vote
    if await close_expired_vote(db, room_id):
        room = await locked_host_room(db, room_id, user.id)
    if await db.scalar(select(VoteRound.id).where(
        VoteRound.room_id == room_id, VoteRound.status == "open")) is not None:
        raise HTTPException(status_code=409, detail="Đóng vòng vote đang mở trước khi kết thúc trận")
    room.status = "finished"
    room.is_paused = False
    room.result_label = result
    room.result_reason = reason
    room.ended_at = now_utc()
    await append_event(db, room_id, user.id, "game.finished", "room",
                       {"result_label": result, "result_reason": reason,
                        "phase_type": room.phase_type, "round_number": room.round_number,
                        "request": request}, client_request_id=body.client_request_id)
    await cancel_all_scheduled(db, room_id, user.id, "Trận kết thúc trước khi chuyển pha")
    await db.commit()
    return await room_output(db, room, user.id)
