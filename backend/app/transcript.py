"""Postgame, append-only room transcript and deterministic exports."""

import csv
import json
from bisect import bisect_right
from datetime import datetime
from io import StringIO
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access
from app.db.models import GameEvent, RoleSnapshot, Room, RoomMember, User
from app.db.session import get_db

router = APIRouter(prefix="/api/rooms/{room_id}/transcript", tags=["transcript"])
Category = Literal["all", "room", "member", "role_set", "role", "roles", "game",
                   "player", "chat", "vote", "event", "content"]
LIMITATION = ("Nhật ký ghi lại dữ liệu và thao tác đã diễn ra trên web. "
              "Nó không tự chứng minh quản trò áp dụng luật role đúng hoặc ghi nhận việc ngoài web.")


class TranscriptEntry(BaseModel):
    id: UUID
    sequence: int
    created_at: datetime
    actor_user_id: UUID | None
    client_request_id: UUID | None
    actor_name: str | None
    kind: str
    visibility: str
    phase_type: str | None
    round_number: int | None
    subject_user_id: UUID | None
    subject_name: str | None
    reason: str | None
    before: Any | None
    after: Any | None
    related_event_ids: list[str]
    references: dict[str, str]
    payload: dict


class TranscriptInfo(BaseModel):
    room_id: UUID
    room_name: str
    result_label: str | None
    result_reason: str | None
    ended_at: datetime | None
    members: dict[str, str]
    roles: dict[str, str]
    limitation: str


class TranscriptPage(BaseModel):
    info: TranscriptInfo
    events: list[TranscriptEntry]
    next_after_sequence: int | None
    total: int


async def finished_room(db: AsyncSession, room_id: UUID, user_id: UUID) -> Room:
    await require_room_access(db, room_id, user_id)
    room = await db.get(Room, room_id)
    if room.status != "finished":
        raise HTTPException(status_code=409, detail="Nhật ký đầy đủ chỉ mở sau khi kết thúc trận")
    return room


async def transcript_info(db: AsyncSession, room: Room) -> TranscriptInfo:
    people = (await db.execute(select(RoomMember.user_id, User.display_name).join(
        User, User.id == RoomMember.user_id).where(RoomMember.room_id == room.id))).all()
    names = {str(room.owner_user_id): (await db.get(User, room.owner_user_id)).display_name}
    names.update({str(user_id): name for user_id, name in people})
    snapshot = await db.get(RoleSnapshot, room.id)
    roles = ({item["id"]: item["name"] for item in snapshot.definitions}
             if snapshot is not None else {})
    return TranscriptInfo(room_id=room.id, room_name=room.name,
                          result_label=room.result_label, result_reason=room.result_reason,
                          ended_at=room.ended_at, members=names, roles=roles,
                          limitation=LIMITATION)


async def phase_markers(db: AsyncSession, room_id: UUID) -> tuple[list[int], list[tuple[str, int]]]:
    events = (await db.scalars(select(GameEvent).where(
        GameEvent.room_id == room_id,
        GameEvent.kind.in_(("game.started", "game.phase_advanced"))
    ).order_by(GameEvent.sequence))).all()
    sequences: list[int] = []
    phases: list[tuple[str, int]] = []
    for event in events:
        data = event.payload.get("current", event.payload)
        if isinstance(data, dict) and data.get("phase_type") in {"day", "night"}:
            sequences.append(event.sequence)
            phases.append((data["phase_type"], data["round_number"]))
    return sequences, phases


def event_phase(event: GameEvent, markers: tuple[list[int], list[tuple[str, int]]]) -> tuple[str | None, int | None]:
    data = event.payload.get("current", event.payload)
    if isinstance(data, dict) and data.get("phase_type") in {"day", "night"} and isinstance(data.get("round_number"), int):
        return data["phase_type"], data["round_number"]
    sequences, phases = markers
    index = bisect_right(sequences, event.sequence) - 1
    return phases[index] if index >= 0 else (None, None)


def event_subject(payload: dict) -> str | None:
    request = payload.get("request")
    candidates = [
        request.get("user_id") if isinstance(request, dict) else None,
        request.get("target_user_id") if isinstance(request, dict) else None,
        request.get("related_user_id") if isinstance(request, dict) else None,
        payload.get("user_id"), payload.get("player_id"),
        (payload.get("related_player") or {}).get("user_id")
        if isinstance(payload.get("related_player"), dict) else None,
        payload.get("participant_user_id"), payload.get("target_user_id"),
    ]
    for value in candidates:
        if isinstance(value, str):
            try:
                UUID(value)
            except ValueError:
                continue
            return value
    return None


def event_reason(payload: dict) -> str | None:
    request = payload.get("request")
    for value in (payload.get("reason"), payload.get("result_reason"),
                  request.get("reason") if isinstance(request, dict) else None,
                  request.get("moderator_reason") if isinstance(request, dict) else None):
        if isinstance(value, str) and value:
            return value
    return None


def event_links(payload: dict) -> list[str]:
    request = payload.get("request")
    values = [payload.get(key) for key in ("source_event_id", "status_event_id", "public_event_id",
                                            "linked_event_id")]
    if isinstance(request, dict):
        values.extend([request.get("source_event_id"), request.get("linked_event_id"),
                       *(request.get("linked_event_ids") or [])])
    values.extend(payload.get("linked_event_ids") or [])
    return list(dict.fromkeys(value for value in values if isinstance(value, str)))


def named_references(payload: Any, info: TranscriptInfo) -> dict[str, str]:
    names = {**info.members, **info.roles}
    found: dict[str, str] = {}

    def scan(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)
        elif isinstance(value, str) and value in names:
            found[value] = names[value]

    scan(payload)
    return dict(sorted(found.items()))


def entry(event: GameEvent, info: TranscriptInfo,
          markers: tuple[list[int], list[tuple[str, int]]]) -> TranscriptEntry:
    payload = event.payload
    subject = event_subject(payload)
    before = payload.get("previous", payload.get("previous_status",
                         payload.get("previous_disposition", payload.get("previous_text"))))
    after = payload.get("current", payload.get("new_disposition", payload.get("new_text")))
    phase_type, round_number = event_phase(event, markers)
    return TranscriptEntry(id=event.id, sequence=event.sequence, created_at=event.created_at,
                           actor_user_id=event.actor_user_id,
                           client_request_id=event.client_request_id,
                           actor_name=info.members.get(str(event.actor_user_id)) if event.actor_user_id else None,
                           kind=event.kind, visibility=event.visibility,
                           phase_type=phase_type, round_number=round_number,
                           subject_user_id=UUID(subject) if subject else None,
                           subject_name=info.members.get(subject) if subject else None,
                           reason=event_reason(payload), before=before, after=after,
                           related_event_ids=event_links(payload),
                           references=named_references(payload, info), payload=payload)


def filtered_events(room_id: UUID, category: Category, query: str):
    statement = select(GameEvent).outerjoin(User, User.id == GameEvent.actor_user_id).where(
        GameEvent.room_id == room_id)
    if category != "all":
        statement = statement.where(GameEvent.kind.like(f"{category}.%"))
    if query:
        needle = f"%{query}%"
        statement = statement.where(or_(cast(GameEvent.id, Text).ilike(needle),
                                        GameEvent.kind.ilike(needle),
                                        cast(GameEvent.payload, Text).ilike(needle),
                                        User.display_name.ilike(needle)))
    return statement


@router.get("", response_model=TranscriptPage)
async def get_transcript(room_id: UUID, response: Response,
                         after_sequence: int = Query(default=0, ge=0),
                         limit: int = Query(default=50, ge=1, le=100),
                         category: Category = "all",
                         q: str = Query(default="", max_length=120),
                         user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)) -> TranscriptPage:
    room = await finished_room(db, room_id, user.id)
    response.headers["Cache-Control"] = "private, no-store"
    info = await transcript_info(db, room)
    query = q.strip()
    base = filtered_events(room_id, category, query)
    total = await db.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
    events = (await db.scalars(base.where(GameEvent.sequence > after_sequence)
                               .order_by(GameEvent.sequence).limit(limit + 1))).all()
    more = len(events) > limit
    page = events[:limit]
    markers = await phase_markers(db, room_id)
    return TranscriptPage(info=info, events=[entry(event, info, markers) for event in page],
                          next_after_sequence=page[-1].sequence if more else None,
                          total=total or 0)


def safe_csv(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


@router.get("/export")
async def export_transcript(room_id: UUID, format: Literal["json", "csv"],
                            category: Category = "all",
                            q: str = Query(default="", max_length=120),
                            user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_db)) -> Response:
    room = await finished_room(db, room_id, user.id)
    info = await transcript_info(db, room)
    markers = await phase_markers(db, room_id)
    events = (await db.scalars(filtered_events(room_id, category, q.strip())
                               .order_by(GameEvent.sequence))).all()
    rows = [entry(event, info, markers).model_dump(mode="json") for event in events]
    headers = {"Cache-Control": "private, no-store",
               "Content-Disposition": f'attachment; filename="ma-soi-{room_id}.{format}"'}
    if format == "json":
        document = {"info": info.model_dump(mode="json"), "events": rows}
        content = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        return Response(content=content, media_type="application/json; charset=utf-8", headers=headers)
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    columns = ("sequence", "id", "created_at", "phase_type", "round_number", "kind", "visibility",
               "actor_user_id", "actor_name", "client_request_id", "subject_user_id", "subject_name", "reason",
               "before", "after", "related_event_ids", "references", "payload")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_csv(json.dumps(row[column], ensure_ascii=False, sort_keys=True)
                              if column in {"before", "after", "related_event_ids", "references", "payload"}
                              else str(row[column]) if row[column] is not None else "")
                         for column in columns])
    return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
