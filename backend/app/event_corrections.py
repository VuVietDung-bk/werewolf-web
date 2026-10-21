"""Append-only corrections for moderator and public game events."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access, require_room_host
from app.db.events import append_event
from app.db.models import ContentRevision, GameEvent, Room, RoomMember, User
from app.db.session import get_db
from app.rooms import locked_host_room

router = APIRouter(prefix="/api/rooms/{room_id}/game/events", tags=["game"])
AnnouncementKind = Literal["general", "death", "revival", "attack", "role", "vote", "other"]
LINKABLE_ANNOUNCEMENT_KINDS = {"player.kill", "player.revive", "player.add_effect",
                               "player.remove_effect", "player.change_role", "player.change_faction",
                               "player.change_team", "vote.decided"}


class PublicPerson(BaseModel):
    user_id: UUID
    display_name: str
    seat_number: int


class LinkableAnnouncementEvent(BaseModel):
    event_id: UUID
    sequence: int
    kind: str
    target_name: str | None
    created_at: datetime


class HostEvent(BaseModel):
    id: UUID
    sequence: int
    kind: str
    visibility: str
    payload: dict
    created_at: datetime


class PublicCorrection(BaseModel):
    id: UUID
    sequence: int
    source_event_id: UUID
    source_sequence: int
    source_kind: str
    text: str
    created_at: datetime
    edited_at: datetime | None


class PublicAnnouncement(BaseModel):
    id: UUID
    sequence: int
    phase_type: str
    round_number: int
    text: str
    kind: AnnouncementKind
    related_player: PublicPerson | None
    source_player: PublicPerson | None
    created_at: datetime
    edited_at: datetime | None


class AnnouncementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    kind: AnnouncementKind = "general"
    related_user_id: UUID | None = None
    source_user_id: UUID | None = None
    linked_event_id: UUID | None = None

    @model_validator(mode="after")
    def strip_text(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Cần nội dung thông báo")
        return self


class AnnouncementOutput(BaseModel):
    event_id: UUID
    repeated: bool


async def public_person(db: AsyncSession, room_id: UUID, user_id: UUID | None) -> dict | None:
    if user_id is None:
        return None
    row = (await db.execute(select(RoomMember, User.display_name).join(
        User, User.id == RoomMember.user_id).where(
            RoomMember.room_id == room_id, RoomMember.user_id == user_id,
            RoomMember.left_at.is_(None)))).one_or_none()
    if row is None:
        raise HTTPException(status_code=422, detail="Người được nêu tên không thuộc phòng")
    member, name = row
    return {"user_id": str(user_id), "display_name": name, "seat_number": member.seat_number}


class CorrectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    source_event_id: UUID
    reason: str = Field(min_length=1, max_length=1000)
    corrected_text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def strip_text(self):
        self.reason = self.reason.strip()
        self.corrected_text = self.corrected_text.strip()
        if not self.reason or not self.corrected_text:
            raise ValueError("Cần lý do và nội dung đính chính")
        return self


class CorrectionOutput(BaseModel):
    event_id: UUID
    repeated: bool
    public_event_id: UUID | None


@router.get("/announcements", response_model=list[PublicAnnouncement])
async def public_announcements(room_id: UUID, user: User = Depends(current_user),
                               db: AsyncSession = Depends(get_db)) -> list[PublicAnnouncement]:
    await require_room_access(db, room_id, user.id)
    events = (await db.scalars(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.kind == "event.custom_announcement",
        GameEvent.visibility == "room",
    ).order_by(GameEvent.sequence))).all()
    revisions = {row.source_event_id: row for row in (await db.scalars(
        select(ContentRevision).where(ContentRevision.room_id == room_id,
                                      ContentRevision.source_event_id.in_([event.id for event in events]))
    )).all()} if events else {}
    return [PublicAnnouncement(
        id=event.id, sequence=event.sequence,
        phase_type=event.payload["phase_type"], round_number=event.payload["round_number"],
        kind=event.payload.get("kind", "general"),
        related_player=event.payload.get("related_player"),
        source_player=event.payload.get("source_player"),
        text=(revisions[event.id].current_text
              if event.id in revisions and revisions[event.id].edited_at
              else event.payload["text"]),
        created_at=event.created_at,
        edited_at=revisions[event.id].edited_at if event.id in revisions else None,
    ) for event in events if event.id not in revisions or revisions[event.id].deleted_at is None]


@router.get("/announcements/linkable-events", response_model=list[LinkableAnnouncementEvent])
async def linkable_announcement_events(room_id: UUID, user: User = Depends(current_user),
                                       db: AsyncSession = Depends(get_db)) -> list[LinkableAnnouncementEvent]:
    await require_room_host(db, room_id, user.id)
    events = (await db.scalars(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.actor_user_id == user.id,
        GameEvent.kind.in_(LINKABLE_ANNOUNCEMENT_KINDS)
    ).order_by(GameEvent.sequence.desc()).limit(50))).all()
    target_ids = {UUID(target_id) for event in events
                  if (target_id := event.payload.get("request", {}).get("user_id")
                      or event.payload.get("request", {}).get("target_user_id"))}
    names = dict((await db.execute(select(User.id, User.display_name).where(
        User.id.in_(target_ids)))).all()) if target_ids else {}
    return [LinkableAnnouncementEvent(
        event_id=event.id, sequence=event.sequence, kind=event.kind,
        target_name=names.get(UUID(target_id)) if (target_id :=
            event.payload.get("request", {}).get("user_id")
            or event.payload.get("request", {}).get("target_user_id")) else None,
        created_at=event.created_at,
    ) for event in events]


@router.post("/announcements", response_model=AnnouncementOutput)
async def publish_announcement(room_id: UUID, body: AnnouncementInput,
                               user: User = Depends(current_user),
                               db: AsyncSession = Depends(get_db)) -> AnnouncementOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"text": body.text, "kind": body.kind,
               "related_user_id": str(body.related_user_id) if body.related_user_id else None,
               "source_user_id": str(body.source_user_id) if body.source_user_id else None,
               "linked_event_id": str(body.linked_event_id) if body.linked_event_id else None}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind == "event.custom_announcement" and request == {
            "text": prior.payload.get("text"), "kind": "general", "related_user_id": None,
            "source_user_id": None, "linked_event_id": None,
        }:
            return AnnouncementOutput(event_id=prior.id, repeated=True)
        if prior.kind != "event.announcement_decided" or prior.actor_user_id != user.id or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return AnnouncementOutput(event_id=UUID(prior.payload["public_event_id"]), repeated=True)
    if room.status != "playing":
        raise HTTPException(status_code=409, detail="Chỉ đăng thông báo khi trận đang chơi")
    related = await public_person(db, room_id, body.related_user_id)
    source = await public_person(db, room_id, body.source_user_id)
    if body.linked_event_id:
        linked = await db.get(GameEvent, body.linked_event_id)
        if (linked is None or linked.room_id != room_id or linked.actor_user_id != user.id
                or linked.kind not in LINKABLE_ANNOUNCEMENT_KINDS):
            raise HTTPException(status_code=422, detail="Thao tác hoặc quyết định vote liên kết không hợp lệ")
    decision = await append_event(db, room_id, user.id, "event.announcement_decided", "host",
                                  {"request": request}, client_request_id=body.client_request_id)
    event = await append_event(db, room_id, user.id, "event.custom_announcement", "room",
                               {"text": body.text, "kind": body.kind,
                                "related_player": related, "source_player": source,
                                "phase_type": room.phase_type, "round_number": room.round_number})
    decision.payload = {**decision.payload, "public_event_id": str(event.id)}
    await db.commit()
    return AnnouncementOutput(event_id=event.id, repeated=False)


@router.get("/host", response_model=list[HostEvent])
async def host_events(room_id: UUID, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> list[HostEvent]:
    await require_room_host(db, room_id, user.id)
    room = await db.get(Room, room_id)
    if room.status not in {"playing", "finished"}:
        raise HTTPException(status_code=409, detail="Trận chưa bắt đầu")
    events = (await db.scalars(select(GameEvent).where(GameEvent.room_id == room_id)
                               .order_by(GameEvent.sequence.desc()).limit(50))).all()
    return [HostEvent(id=event.id, sequence=event.sequence, kind=event.kind,
                      visibility=event.visibility, payload=event.payload,
                      created_at=event.created_at) for event in events]


@router.get("/public-corrections", response_model=list[PublicCorrection])
async def public_corrections(room_id: UUID, user: User = Depends(current_user),
                             db: AsyncSession = Depends(get_db)) -> list[PublicCorrection]:
    await require_room_access(db, room_id, user.id)
    events = (await db.scalars(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.kind == "event.public_correction",
        GameEvent.visibility == "room",
    ).order_by(GameEvent.sequence))).all()
    revisions = {row.source_event_id: row for row in (await db.scalars(
        select(ContentRevision).where(ContentRevision.room_id == room_id,
                                      ContentRevision.source_event_id.in_([event.id for event in events]))
    )).all()} if events else {}
    return [PublicCorrection(id=event.id, sequence=event.sequence,
                             source_event_id=UUID(event.payload["source_event_id"]),
                             source_sequence=event.payload["source_sequence"],
                             source_kind=event.payload["source_kind"],
                             text=(revisions[event.id].current_text
                                   if event.id in revisions and revisions[event.id].edited_at
                                   else event.payload["text"]),
                             created_at=event.created_at,
                             edited_at=revisions[event.id].edited_at if event.id in revisions else None)
            for event in events if event.id not in revisions or revisions[event.id].deleted_at is None]


@router.post("/corrections", response_model=CorrectionOutput)
async def correct_event(room_id: UUID, body: CorrectionInput,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> CorrectionOutput:
    await require_room_host(db, room_id, user.id)
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    if room.status not in {"playing", "finished"}:
        raise HTTPException(status_code=409, detail="Trận chưa bắt đầu")
    request = {"source_event_id": str(body.source_event_id), "reason": body.reason,
               "corrected_text": body.corrected_text}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "event.correction_decided" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return CorrectionOutput(event_id=prior.id, repeated=True,
                                public_event_id=UUID(prior.payload["public_event_id"])
                                if prior.payload.get("public_event_id") else None)
    source = await db.scalar(select(GameEvent).where(
        GameEvent.id == body.source_event_id, GameEvent.room_id == room_id))
    if source is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện trong phòng")
    if source.visibility not in {"host", "room"}:
        raise HTTPException(status_code=409, detail="Sự kiện này chưa hỗ trợ đính chính")
    decision = await append_event(db, room_id, user.id, "event.correction_decided", "host",
                                  {"request": request, "source_sequence": source.sequence,
                                   "source_kind": source.kind},
                                  client_request_id=body.client_request_id)
    public_event_id = None
    if source.visibility == "room":
        public_event = await append_event(db, room_id, user.id, "event.public_correction", "room",
                                          {"source_event_id": str(source.id),
                                           "source_sequence": source.sequence,
                                           "source_kind": source.kind,
                                           "text": body.corrected_text})
        public_event_id = public_event.id
        decision.payload = {**decision.payload, "public_event_id": str(public_event_id)}
    await db.commit()
    return CorrectionOutput(event_id=decision.id, repeated=False, public_event_id=public_event_id)
