"""Room role catalog and moderator controlled random distribution."""

import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access
from app.db.events import append_event
from app.db.models import PlayerState, RoleAssignment, RoleDefinition, RoleIcon, RoleSet, RoleSnapshot, Room, RoomMember, User
from app.db.session import get_db

router = APIRouter(prefix="/api/rooms/{room_id}", tags=["roles"])
Phase = Literal["day", "night"]


class HostContact(BaseModel):
    can_initiate: bool = False
    initiate_phases: list[Phase] = Field(default_factory=list)
    reply_allowed: bool = True
    when_dead: bool = False

    @model_validator(mode="after")
    def valid_phases(self):
        if len(set(self.initiate_phases)) != len(self.initiate_phases):
            raise ValueError("Pha liên hệ quản trò bị lặp")
        if not self.can_initiate and self.initiate_phases:
            raise ValueError("Role không mở chat không được chọn pha mở chat")
        if self.can_initiate and not self.initiate_phases:
            raise ValueError("Role mở chat cần ít nhất một pha được phép")
        return self


class RoleInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=4000)
    faction_label: str = Field(min_length=1, max_length=80)
    seer_result: str = Field(min_length=1, max_length=80)
    team_chat_label: str | None = Field(default=None, max_length=80)
    quantity: int = Field(default=0, ge=0, le=30)
    role_kind: Literal["active", "passive"]
    request_phases: list[Phase] = Field(default_factory=list)
    request_when_dead: bool = False
    host_contact: HostContact = Field(default_factory=HostContact)
    icon_key: Literal["auto", "generic", "wolf", "village", "neutral", "seer", "hunter", "potion", "upload"] = "auto"
    icon_id: UUID | None = None

    @model_validator(mode="after")
    def valid_permissions(self):
        if (not self.name.strip() or not self.description.strip() or
                not self.faction_label.strip() or not self.seer_result.strip()):
            raise ValueError("Tên, mô tả, phe và kết quả Tiên Tri soi ra không được để trống")
        if len(set(self.request_phases)) != len(self.request_phases):
            raise ValueError("Pha gửi yêu cầu bị lặp")
        if self.role_kind == "passive" and (self.request_phases or self.request_when_dead
                                            or self.host_contact.can_initiate):
            raise ValueError("Role bị động không thể chủ động gửi yêu cầu hoặc mở chat quản trò")
        if self.role_kind == "active" and not self.request_phases and self.request_when_dead:
            raise ValueError("Yêu cầu sau khi chết cần ít nhất một pha được phép")
        if (self.icon_key == "upload") != (self.icon_id is not None):
            raise ValueError("Icon tải lên cần ID ảnh; icon mẫu không dùng ID ảnh")
        return self


class SetNameInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class QuantityInput(BaseModel):
    quantity: int = Field(ge=0, le=30)


class RoleOutput(RoleInput):
    id: UUID


class CatalogOutput(BaseModel):
    name: str
    locked: bool
    distribution_mode: str | None
    roles: list[RoleOutput]
    draft_config: dict | None = None


class AssignmentOutput(BaseModel):
    user_id: UUID
    role: RoleOutput


def normalize(body: RoleInput) -> dict:
    data = body.model_dump()
    for key in ("name", "description", "faction_label", "seer_result"):
        data[key] = data[key].strip()
    data["team_chat_label"] = (data["team_chat_label"] or "").strip() or None
    return data


def role_data(role: RoleDefinition) -> dict:
    return {
        "id": str(role.id), "name": role.name, "description": role.description,
        "faction_label": role.faction_label, "seer_result": role.seer_result,
        "team_chat_label": role.team_chat_label,
        "quantity": role.quantity, "role_kind": role.role_kind,
        "request_phases": role.request_phases, "request_when_dead": role.request_when_dead,
        "host_contact": role.host_contact,
        "icon_key": role.icon_key,
        "icon_id": str(role.icon_id) if role.icon_id else None,
    }


def snapshot_role_data(role: dict) -> dict:
    # Snapshots created before this field existed remain readable without rewriting history.
    return {"seer_result": "Chưa thiết lập", "icon_key": "auto", "icon_id": None, **role}


def effective_role_data(definitions: dict[str, dict], assigned_role_id: UUID,
                        state: PlayerState | None) -> dict:
    role_id = str(state.current_role_id or assigned_role_id) if state else str(assigned_role_id)
    data = snapshot_role_data(definitions[role_id]).copy()
    if state is not None:
        if state.faction_label_override is not None:
            data["faction_label"] = state.faction_label_override
        if state.team_chat_override_set:
            data["team_chat_label"] = state.team_chat_label_override
    return data


async def validate_role_icon(db: AsyncSession, room_id: UUID, body: RoleInput) -> None:
    if body.icon_id is None:
        return
    icon = await db.get(RoleIcon, body.icon_id)
    if icon is None or icon.room_id != room_id:
        raise HTTPException(status_code=422, detail="Icon tải lên không thuộc phòng này")


async def locked_host_room(db: AsyncSession, room_id: UUID, user_id: UUID) -> Room:
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")
    if room.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Chỉ quản trò được thực hiện")
    if room.status not in {"open", "closed"}:
        raise HTTPException(status_code=409, detail="Bộ role đã khóa cho ván này")
    return room


async def check_unique_name(db: AsyncSession, room_id: UUID, name: str, except_id: UUID | None = None) -> None:
    roles = (await db.scalars(select(RoleDefinition).where(RoleDefinition.room_id == room_id))).all()
    if any(role.id != except_id and role.name.casefold() == name.casefold() for role in roles):
        raise HTTPException(status_code=409, detail="Tên role đã có trong bộ này")


@router.get("/role-catalog", response_model=CatalogOutput)
async def get_catalog(room_id: UUID, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> CatalogOutput:
    await require_room_access(db, room_id, user.id)
    snapshot = await db.get(RoleSnapshot, room_id)
    if snapshot is not None:
        public_draft = ({"candidate_role_ids": snapshot.settings["candidate_role_ids"]}
                        if snapshot.distribution_mode == "draft" else None)
        return CatalogOutput(name=snapshot.set_name, locked=True,
                             distribution_mode=snapshot.distribution_mode,
                             draft_config=public_draft,
                             roles=[RoleOutput.model_validate(snapshot_role_data(role))
                                    for role in snapshot.definitions])
    role_set = await db.get(RoleSet, room_id)
    roles = (await db.scalars(select(RoleDefinition).where(RoleDefinition.room_id == room_id)
                              .order_by(RoleDefinition.name))).all()
    return CatalogOutput(name=role_set.name if role_set else "Bộ role của phòng", locked=False,
                         distribution_mode=None,
                         roles=[RoleOutput.model_validate(role_data(role)) for role in roles])


@router.patch("/role-set", response_model=CatalogOutput)
async def rename_set(room_id: UUID, body: SetNameInput, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)) -> CatalogOutput:
    await locked_host_room(db, room_id, user.id)
    role_set = await db.get(RoleSet, room_id)
    if role_set is None:
        role_set = RoleSet(room_id=room_id)
        db.add(role_set)
    role_set.name = body.name.strip()
    if not role_set.name:
        raise HTTPException(status_code=422, detail="Tên bộ role không được để trống")
    await append_event(db, room_id, user.id, "role_set.renamed", "room", {"name": role_set.name})
    await db.commit()
    return await get_catalog(room_id, user, db)


@router.post("/roles", response_model=RoleOutput, status_code=201)
async def create_role(room_id: UUID, body: RoleInput, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> RoleOutput:
    await locked_host_room(db, room_id, user.id)
    data = normalize(body)
    await validate_role_icon(db, room_id, body)
    await check_unique_name(db, room_id, data["name"])
    role = RoleDefinition(room_id=room_id, **data)
    db.add(role)
    await db.flush()
    await append_event(db, room_id, user.id, "role.created", "room", role_data(role))
    await db.commit()
    return RoleOutput.model_validate(role_data(role))


@router.put("/roles/{role_id}", response_model=RoleOutput)
async def update_role(room_id: UUID, role_id: UUID, body: RoleInput,
                      user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> RoleOutput:
    await locked_host_room(db, room_id, user.id)
    role = await db.get(RoleDefinition, role_id)
    if role is None or role.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy role")
    data = normalize(body)
    await validate_role_icon(db, room_id, body)
    await check_unique_name(db, room_id, data["name"], role_id)
    for key, value in data.items():
        setattr(role, key, value)
    await append_event(db, room_id, user.id, "role.updated", "room", role_data(role))
    await db.commit()
    return RoleOutput.model_validate(role_data(role))


@router.patch("/roles/{role_id}/quantity", response_model=RoleOutput)
async def update_role_quantity(room_id: UUID, role_id: UUID, body: QuantityInput,
                               user: User = Depends(current_user),
                               db: AsyncSession = Depends(get_db)) -> RoleOutput:
    await locked_host_room(db, room_id, user.id)
    role = await db.get(RoleDefinition, role_id)
    if role is None or role.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy role")
    if role.quantity != body.quantity:
        previous = role.quantity
        role.quantity = body.quantity
        await append_event(db, room_id, user.id, "role.quantity_updated", "room",
                           {"role_id": str(role_id), "name": role.name,
                            "previous": previous, "quantity": body.quantity})
        await db.commit()
    return RoleOutput.model_validate(role_data(role))


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(room_id: UUID, role_id: UUID, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> None:
    await locked_host_room(db, room_id, user.id)
    role = await db.get(RoleDefinition, role_id)
    if role is None or role.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy role")
    await db.delete(role)
    await append_event(db, room_id, user.id, "role.deleted", "room",
                       {"role_id": str(role_id), "name": role.name})
    await db.commit()


SAMPLE_ROLES = [
    {"name": "Dân làng", "description": "Thảo luận và bỏ phiếu cùng làng. Không có yêu cầu chủ động.",
     "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "passive"},
    {"name": "Tiên Tri", "description": "Mỗi đêm đề nghị quản trò cho biết vai trò của một người chơi. Quản trò trả lời riêng.",
     "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "active", "request_phases": ["night"], "icon_key": "seer",
     "host_contact": {"can_initiate": True, "initiate_phases": ["night"]}},
    {"name": "Sói thường", "description": "Mỗi đêm bàn với đội Sói và gửi đề nghị giết cho quản trò. Quản trò quyết định kết quả.",
     "faction_label": "Sói", "seer_result": "Ác", "team_chat_label": "Sói", "role_kind": "active", "icon_key": "wolf",
     "request_phases": ["night"],
     "host_contact": {"can_initiate": True, "initiate_phases": ["night"]}},
    {"name": "Xạ thủ", "description": "Ban ngày có thể đề nghị bắn một người. Quản trò tự quyết định kết quả và số lượt dùng theo luật phòng.",
     "faction_label": "Làng", "seer_result": "Không rõ", "role_kind": "active", "request_phases": ["day"], "icon_key": "hunter",
     "host_contact": {"can_initiate": True, "initiate_phases": ["day"]}},
]


@router.post("/role-set/sample", response_model=CatalogOutput)
async def add_sample_roles(room_id: UUID, user: User = Depends(current_user),
                           db: AsyncSession = Depends(get_db)) -> CatalogOutput:
    await locked_host_room(db, room_id, user.id)
    existing = (await db.scalars(select(RoleDefinition).where(RoleDefinition.room_id == room_id))).all()
    if existing:
        raise HTTPException(status_code=409, detail="Bộ role đã có nội dung")
    for sample in SAMPLE_ROLES:
        db.add(RoleDefinition(room_id=room_id, **normalize(RoleInput.model_validate(sample))))
    await append_event(db, room_id, user.id, "role_set.sample_added", "room",
                       {"names": [role["name"] for role in SAMPLE_ROLES]})
    await db.commit()
    return await get_catalog(room_id, user, db)


@router.post("/roles/distribute-random", response_model=list[AssignmentOutput])
async def distribute_random(room_id: UUID, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_db)) -> list[AssignmentOutput]:
    room = await locked_host_room(db, room_id, user.id)
    if room.status != "closed":
        raise HTTPException(status_code=409, detail="Đóng đăng ký trước khi phát role")
    members = (await db.scalars(select(RoomMember).where(RoomMember.room_id == room_id,
                           RoomMember.left_at.is_(None)).order_by(RoomMember.seat_number))).all()
    roles = (await db.scalars(select(RoleDefinition).where(RoleDefinition.room_id == room_id)
                              .order_by(RoleDefinition.name))).all()
    if len(members) < 2:
        raise HTTPException(status_code=409, detail="Cần ít nhất hai người chơi")
    if sum(role.quantity for role in roles) != len(members):
        raise HTTPException(status_code=409, detail="Tổng số bản role phải bằng số người đang ở phòng")
    slots = [role for role in roles for _ in range(role.quantity)]
    secrets.SystemRandom().shuffle(slots)
    role_set = await db.get(RoleSet, room_id)
    snapshot = RoleSnapshot(room_id=room_id, set_name=role_set.name,
                            definitions=[role_data(role) for role in roles],
                            distribution_mode="random")
    db.add(snapshot)
    await db.flush()
    assignments = []
    for member, role in zip(members, slots):
        db.add(RoleAssignment(room_id=room_id, user_id=member.user_id, role_id=role.id))
        assignments.append(AssignmentOutput(user_id=member.user_id,
                                            role=RoleOutput.model_validate(role_data(role))))
    room.status = "assigned"
    await append_event(db, room_id, user.id, "roles.distributed", "host",
                       {"mode": "random", "assignments": [
                           {"user_id": str(item.user_id), "role_id": str(item.role.id)}
                           for item in assignments]})
    await db.commit()
    return assignments


async def snapshot_assignments(db: AsyncSession, room_id: UUID) -> list[AssignmentOutput]:
    snapshot = await db.get(RoleSnapshot, room_id)
    if snapshot is None:
        return []
    by_id = {role["id"]: role for role in snapshot.definitions}
    rows = (await db.scalars(select(RoleAssignment).where(RoleAssignment.room_id == room_id))).all()
    states = (await db.scalars(select(PlayerState).where(PlayerState.room_id == room_id))).all()
    by_user = {state.user_id: state for state in states}
    return [AssignmentOutput(user_id=row.user_id,
                             role=RoleOutput.model_validate(effective_role_data(
                                 by_id, row.role_id, by_user.get(row.user_id))))
            for row in rows]


@router.get("/roles/my-assignment", response_model=AssignmentOutput | None)
async def my_assignment(room_id: UUID, user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> AssignmentOutput | None:
    is_host = await require_room_access(db, room_id, user.id)
    if is_host:
        return None
    return next((item for item in await snapshot_assignments(db, room_id)
                 if item.user_id == user.id), None)


@router.get("/roles/assignments", response_model=list[AssignmentOutput])
async def all_assignments(room_id: UUID, user: User = Depends(current_user),
                          db: AsyncSession = Depends(get_db)) -> list[AssignmentOutput]:
    is_host = await require_room_access(db, room_id, user.id)
    if not is_host:
        raise HTTPException(status_code=403, detail="Chỉ quản trò xem được toàn bộ role")
    return await snapshot_assignments(db, room_id)
