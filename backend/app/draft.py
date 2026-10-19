"""One-at-a-time 1-of-3 role draft; room row serializes every turn."""

import secrets
import unicodedata
from collections import Counter, defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, now_utc, require_room_access
from app.db.events import append_event
from app.db.models import DraftTurn, RoleAssignment, RoleDefinition, RoleSet, RoleSnapshot, Room, RoomMember, User
from app.db.session import get_db
from app.roles import RoleOutput, locked_host_room, role_data, snapshot_role_data

router = APIRouter(prefix="/api/rooms/{room_id}/roles/draft", tags=["roles"])
random_source = secrets.SystemRandom()


class DraftStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_role_ids: list[UUID]
    allow_duplicate_roles: bool = True

    @model_validator(mode="after")
    def unique_values(self):
        if len(set(self.candidate_role_ids)) != len(self.candidate_role_ids):
            raise ValueError("Role ứng viên bị lặp")
        return self


class DraftChoiceInput(BaseModel):
    role_id: UUID | None = None
    choose_random: bool = False

    @model_validator(mode="after")
    def exactly_one_choice(self):
        if (self.role_id is None) == (not self.choose_random):
            raise ValueError("Chọn một role được mời hoặc chọn ngẫu nhiên")
        return self


class HostRandomInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class DraftTurnOutput(BaseModel):
    turn_index: int
    user_id: UUID
    offers: list[UUID] | None
    chosen_role_id: UUID | None
    chosen_by_user_id: UUID | None
    choice_mode: str | None
    choice_reason: str | None


class DraftStatusOutput(BaseModel):
    mode: str | None
    status: str
    total: int
    completed: int
    current_turn: int | None
    my_turn: int | None
    my_offers: list[RoleOutput] | None
    faction_seats: dict[str, int] | None
    candidate_role_ids: list[UUID] | None
    allow_duplicate_roles: bool | None
    host_turns: list[DraftTurnOutput] | None


def faction_key(label: str) -> str:
    return label.strip().casefold()


def priority_kind(label: str) -> str | None:
    plain = "".join(char for char in unicodedata.normalize("NFD", label.casefold())
                    if unicodedata.category(char) != "Mn").replace("đ", "d").strip()
    if plain in {"soi", "ma soi", "phe soi", "wolf", "wolves"}:
        return "wolf"
    if plain in {"trung lap", "phe trung lap", "neutral"}:
        return "neutral"
    return None


def choose_offers_legacy(snapshot: RoleSnapshot, turns: list[DraftTurn]) -> list[str]:
    definitions = {role["id"]: role for role in snapshot.definitions}
    chosen_ids = {str(turn.chosen_role_id) for turn in turns if turn.chosen_role_id is not None}
    used_seats = Counter(faction_key(definitions[str(turn.chosen_role_id)]["faction_label"])
                         for turn in turns if turn.chosen_role_id is not None)
    quotas = {faction_key(label): seats for label, seats in snapshot.settings["faction_seats"].items()}
    candidates = [definitions[role_id] for role_id in snapshot.settings["candidate_role_ids"]]
    pools: dict[str, list[str]] = defaultdict(list)
    for role in candidates:
        key = faction_key(role["faction_label"])
        if quotas.get(key, 0) > used_seats[key] and (snapshot.settings["allow_duplicate_roles"]
                                               or role["id"] not in chosen_ids):
            pools[key].append(role["id"])
    eligible = [role_id for pool in pools.values() for role_id in pool]
    if len(eligible) < 3:
        raise RuntimeError("Cấu hình draft không còn đủ ba role hợp lệ")
    offers: list[str] = []
    for kind in ("wolf", "neutral"):
        matching = [role_id for key, pool in pools.items()
                    if priority_kind(definitions[pool[0]]["faction_label"]) == kind
                    for role_id in pool if role_id not in offers]
        if matching:
            offers.append(random_source.choice(matching))
    offered_factions = {faction_key(definitions[role_id]["faction_label"]) for role_id in offers}
    distinct_factions = [key for key in pools if key not in offered_factions]
    random_source.shuffle(distinct_factions)
    for key in distinct_factions:
        if len(offers) == 3:
            break
        offers.append(random_source.choice(pools[key]))
    remaining = [role_id for role_id in eligible if role_id not in offers]
    offers.extend(random_source.sample(remaining, 3 - len(offers)))
    random_source.shuffle(offers)
    return offers


def eligible_role_ids(snapshot: RoleSnapshot, turns: list[DraftTurn]) -> list[str]:
    chosen_ids = {str(turn.chosen_role_id) for turn in turns if turn.chosen_role_id is not None}
    return [role_id for role_id in snapshot.settings["candidate_role_ids"]
            if snapshot.settings["allow_duplicate_roles"] or role_id not in chosen_ids]


def choose_offers(snapshot: RoleSnapshot, turns: list[DraftTurn]) -> list[str]:
    # Existing drafts retain their saved quota rules; new drafts use an open pool.
    if "faction_seats" in snapshot.settings:
        return choose_offers_legacy(snapshot, turns)
    eligible = eligible_role_ids(snapshot, turns)
    if not eligible:
        raise RuntimeError("Không còn role hợp lệ cho lượt chọn")
    return random_source.sample(eligible, min(3, len(eligible)))


async def all_turns(db: AsyncSession, room_id: UUID) -> list[DraftTurn]:
    return (await db.scalars(select(DraftTurn).where(DraftTurn.room_id == room_id)
                             .order_by(DraftTurn.turn_index))).all()


@router.post("/start", response_model=DraftStatusOutput)
async def start_draft(room_id: UUID, body: DraftStartInput, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> DraftStatusOutput:
    room = await locked_host_room(db, room_id, user.id)
    if room.status != "closed":
        raise HTTPException(status_code=409, detail="Đóng đăng ký trước khi bắt đầu chọn role")
    members = (await db.scalars(select(RoomMember).where(RoomMember.room_id == room_id,
                           RoomMember.left_at.is_(None)).order_by(RoomMember.seat_number))).all()
    roles = (await db.scalars(select(RoleDefinition).where(RoleDefinition.room_id == room_id))).all()
    if len(members) < 2:
        raise HTTPException(status_code=409, detail="Cần ít nhất hai người chơi")
    role_by_id = {role.id: role for role in roles}
    if not body.candidate_role_ids:
        raise HTTPException(status_code=409, detail="Cần chọn ít nhất một role ứng viên")
    if not set(body.candidate_role_ids).issubset(role_by_id):
        raise HTTPException(status_code=422, detail="Role ứng viên không thuộc bộ role")
    if not body.allow_duplicate_roles and len(body.candidate_role_ids) < len(members):
        raise HTTPException(status_code=409, detail=(
            "Khi tắt role trùng, cần ít nhất một role ứng viên cho mỗi người chơi"))
    order = [member.user_id for member in members]
    random_source.shuffle(order)
    role_set = await db.get(RoleSet, room_id)
    settings = {
        "candidate_role_ids": [str(role_id) for role_id in body.candidate_role_ids],
        "allow_duplicate_roles": body.allow_duplicate_roles,
        "turn_order": [str(user_id) for user_id in order],
    }
    snapshot = RoleSnapshot(room_id=room_id, set_name=role_set.name,
                            definitions=[role_data(role) for role in roles],
                            distribution_mode="draft", settings=settings)
    db.add(snapshot)
    await db.flush()
    turns = [DraftTurn(room_id=room_id, turn_index=index, user_id=user_id)
             for index, user_id in enumerate(order, start=1)]
    turns[0].offers = choose_offers(snapshot, turns)
    turns[0].offered_at = now_utc()
    db.add_all(turns)
    room.status = "drafting"
    await append_event(db, room_id, user.id, "roles.draft_started", "host",
                       {**settings, "first_offers": turns[0].offers})
    await db.commit()
    return await draft_status(room_id, user, db)


@router.get("/status", response_model=DraftStatusOutput)
async def draft_status(room_id: UUID, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)) -> DraftStatusOutput:
    is_host = await require_room_access(db, room_id, user.id)
    room = await db.get(Room, room_id)
    snapshot = await db.get(RoleSnapshot, room_id)
    if snapshot is None:
        return DraftStatusOutput(mode=None, status=room.status, total=0, completed=0,
                                 current_turn=None, my_turn=None, my_offers=None,
                                 faction_seats=None, candidate_role_ids=None,
                                 allow_duplicate_roles=None, host_turns=None)
    if snapshot.distribution_mode != "draft":
        return DraftStatusOutput(mode=snapshot.distribution_mode, status=room.status, total=0,
                                 completed=0, current_turn=None, my_turn=None, my_offers=None,
                                 faction_seats=None, candidate_role_ids=None,
                                 allow_duplicate_roles=None, host_turns=None)
    turns = await all_turns(db, room_id)
    current = next((turn for turn in turns if turn.chosen_role_id is None), None)
    own = next((turn for turn in turns if turn.user_id == user.id), None)
    by_id = {role["id"]: role for role in snapshot.definitions}
    my_offers = ([RoleOutput.model_validate(snapshot_role_data(by_id[role_id]))
                  for role_id in own.offers] if own is not None and own is current and own.offers else None)
    return DraftStatusOutput(
        mode="draft", status=room.status, total=len(turns),
        completed=sum(turn.chosen_role_id is not None for turn in turns),
        current_turn=current.turn_index if current else None,
        my_turn=own.turn_index if own else None, my_offers=my_offers,
        faction_seats=snapshot.settings.get("faction_seats"),
        candidate_role_ids=snapshot.settings["candidate_role_ids"],
        allow_duplicate_roles=snapshot.settings["allow_duplicate_roles"],
        host_turns=[DraftTurnOutput(turn_index=turn.turn_index, user_id=turn.user_id,
                                   offers=turn.offers, chosen_role_id=turn.chosen_role_id,
                                   chosen_by_user_id=turn.chosen_by_user_id,
                                   choice_mode=turn.choice_mode,
                                   choice_reason=turn.choice_reason)
                    for turn in turns] if is_host else None,
    )


async def resolve_choice(db: AsyncSession, room: Room, snapshot: RoleSnapshot,
                         turn: DraftTurn, turns: list[DraftTurn], actor: User,
                         chosen_id: UUID, mode: str, reason: str | None) -> None:
    allowed = (eligible_role_ids(snapshot, turns) if mode in {"self_random", "host_random"}
               and "faction_seats" not in snapshot.settings else turn.offers)
    if str(chosen_id) not in allowed:
        raise HTTPException(status_code=409, detail="Role không hợp lệ cho lượt này")
    turn.chosen_role_id = chosen_id
    turn.chosen_by_user_id = actor.id
    turn.choice_mode = mode
    turn.choice_reason = reason
    turn.chosen_at = now_utc()
    db.add(RoleAssignment(room_id=room.id, user_id=turn.user_id, role_id=chosen_id))
    next_turn = next((item for item in turns if item.chosen_role_id is None), None)
    if next_turn:
        next_turn.offers = choose_offers(snapshot, turns)
        next_turn.offered_at = now_utc()
    else:
        room.status = "assigned"
    await append_event(db, room.id, actor.id, "roles.draft_chosen", "host",
                       {"turn_index": turn.turn_index, "user_id": str(turn.user_id),
                        "offers": turn.offers, "chosen_role_id": str(chosen_id),
                        "choice_mode": mode, "reason": reason})
    if next_turn:
        await append_event(db, room.id, actor.id, "roles.draft_offered", "host",
                           {"turn_index": next_turn.turn_index, "user_id": str(next_turn.user_id),
                            "offers": next_turn.offers})
    else:
        await append_event(db, room.id, actor.id, "roles.draft_completed", "room",
                           {"total": len(turns)})
    await db.commit()


@router.post("/choose", response_model=DraftStatusOutput)
async def choose_draft_role(room_id: UUID, body: DraftChoiceInput,
                            user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_db)) -> DraftStatusOutput:
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")
    if room.status != "drafting":
        raise HTTPException(status_code=409, detail="Không có lượt chọn role đang mở")
    await require_room_access(db, room_id, user.id)
    snapshot = await db.get(RoleSnapshot, room_id)
    turns = await all_turns(db, room_id)
    turn = next((item for item in turns if item.chosen_role_id is None), None)
    if turn is None or turn.user_id != user.id:
        raise HTTPException(status_code=403, detail="Chưa đến lượt của bạn")
    random_pool = (eligible_role_ids(snapshot, turns) if "faction_seats" not in snapshot.settings
                   else turn.offers)
    chosen_id = random_source.choice(random_pool) if body.choose_random else body.role_id
    await resolve_choice(db, room, snapshot, turn, turns, user, chosen_id,
                         "self_random" if body.choose_random else "self", None)
    return await draft_status(room_id, user, db)


@router.post("/host-random", response_model=DraftStatusOutput)
async def host_random_choice(room_id: UUID, body: HostRandomInput,
                             user: User = Depends(current_user),
                             db: AsyncSession = Depends(get_db)) -> DraftStatusOutput:
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")
    if room.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Chỉ quản trò được chọn hộ")
    if room.status != "drafting":
        raise HTTPException(status_code=409, detail="Không có lượt chọn role đang mở")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Cần ghi lý do chọn hộ")
    snapshot = await db.get(RoleSnapshot, room_id)
    turns = await all_turns(db, room_id)
    turn = next((item for item in turns if item.chosen_role_id is None), None)
    random_pool = (eligible_role_ids(snapshot, turns) if "faction_seats" not in snapshot.settings
                   else turn.offers)
    await resolve_choice(db, room, snapshot, turn, turns, user,
                         random_source.choice(random_pool), "host_random", reason)
    return await draft_status(room_id, user, db)
