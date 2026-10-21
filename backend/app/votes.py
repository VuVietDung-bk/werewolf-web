"""Daytime ballots are recorded; only the moderator decides the result."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, now_utc, require_room_access
from app.db.events import append_event
from app.db.models import ContentRevision, GameEvent, PlayerState, Room, RoomMember, User, VoteBallot, VoteRound
from app.db.session import get_db
from app.rooms import locked_host_room

router = APIRouter(prefix="/api/rooms/{room_id}/votes", tags=["votes"])


class VoteCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID


class BallotInput(VoteCommand):
    choice: Literal["target", "skip", "withdraw"]
    target_user_id: UUID | None = None

    @model_validator(mode="after")
    def check_target(self):
        if (self.choice == "target") != (self.target_user_id is not None):
            raise ValueError("Chỉ phiếu chọn người mới có mục tiêu")
        return self


class VoteRoundOutput(BaseModel):
    id: UUID
    day_number: int
    status: Literal["open", "closed"]
    opened_at: datetime
    closed_at: datetime | None
    eligible: bool
    my_choice: Literal["target", "skip", "withdrawn"] | None
    my_target_user_id: UUID | None
    submitted_count: int | None


class VoteHostRoundOutput(VoteRoundOutput):
    decided_at: datetime | None
    published_at: datetime | None


class VoteMutationOutput(BaseModel):
    event_id: UUID
    vote_round_id: UUID
    repeated: bool


class VoteDecisionInput(VoteCommand):
    decision_text: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=1000)
    target_user_id: UUID | None = None

    @model_validator(mode="after")
    def trim_required(self):
        self.decision_text = self.decision_text.strip()
        self.reason = self.reason.strip()
        if not self.decision_text or not self.reason:
            raise ValueError("Cần nhập quyết định và lý do")
        return self


class VoteBallotReview(BaseModel):
    voter_user_id: UUID
    display_name: str
    seat_number: int
    public_is_alive: bool
    choice: Literal["target", "skip", "withdrawn"] | None
    target_user_id: UUID | None


class VoteTally(BaseModel):
    target_user_id: UUID
    display_name: str
    seat_number: int
    count: int


class VoteDecisionReview(BaseModel):
    decision_text: str
    reason: str
    target_user_id: UUID | None
    event_id: UUID
    decided_at: datetime


class VoteReviewOutput(BaseModel):
    vote_round_id: UUID
    ballots: list[VoteBallotReview]
    tally: list[VoteTally]
    skip_count: int
    withdrawn_count: int
    not_submitted_count: int
    decision: VoteDecisionReview | None
    publication_event_id: UUID | None
    publication_deleted: bool


class VotePublicationInput(VoteCommand):
    include_result: bool = True
    include_voter_names: bool = False
    include_reason: bool = False

    @model_validator(mode="after")
    def at_least_one_part(self):
        if not (self.include_result or self.include_voter_names or self.include_reason):
            raise ValueError("Chọn ít nhất một phần để công bố")
        return self


class VotePublicationOutput(BaseModel):
    event_id: UUID
    public_event_id: UUID
    vote_round_id: UUID
    repeated: bool


class PublicVoteNotice(BaseModel):
    id: UUID
    vote_round_id: UUID
    day_number: int
    text: str
    created_at: datetime
    edited_at: datetime | None


async def review_for_host(db: AsyncSession, room_id: UUID,
                          vote: VoteRound) -> VoteReviewOutput:
    members = (await db.execute(select(RoomMember, User, PlayerState)
        .join(User, User.id == RoomMember.user_id)
        .join(PlayerState, (PlayerState.room_id == RoomMember.room_id)
              & (PlayerState.user_id == RoomMember.user_id))
        .where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
        .order_by(RoomMember.seat_number))).all()
    ballots_by_user = {ballot.voter_user_id: ballot for ballot in (await db.scalars(
        select(VoteBallot).where(VoteBallot.vote_round_id == vote.id))).all()}
    rows = [VoteBallotReview(
        voter_user_id=member.user_id, display_name=person.display_name,
        seat_number=member.seat_number, public_is_alive=state.public_is_alive,
        choice=ballots_by_user[member.user_id].choice if member.user_id in ballots_by_user else None,
        target_user_id=ballots_by_user[member.user_id].target_user_id
        if member.user_id in ballots_by_user else None,
    ) for member, person, state in members]
    counts: dict[UUID, int] = {}
    for row in rows:
        if row.choice == "target" and row.target_user_id is not None:
            counts[row.target_user_id] = counts.get(row.target_user_id, 0) + 1
    people = {member.user_id: (person.display_name, member.seat_number)
              for member, person, _ in members}
    tally = [VoteTally(target_user_id=target_id, display_name=people[target_id][0],
                       seat_number=people[target_id][1], count=count)
             for target_id, count in counts.items() if target_id in people]
    tally.sort(key=lambda item: (-item.count, item.seat_number))
    decision = VoteDecisionReview(
        decision_text=vote.decision_text, reason=vote.decision_reason,
        target_user_id=vote.decision_target_user_id,
        event_id=vote.decision_event_id, decided_at=vote.decided_at,
    ) if vote.decided_at is not None else None
    publication_revision = (await db.get(ContentRevision, vote.publication_event_id)
                            if vote.publication_event_id else None)
    return VoteReviewOutput(
        vote_round_id=vote.id, ballots=rows, tally=tally,
        skip_count=sum(row.choice == "skip" for row in rows),
        withdrawn_count=sum(row.choice == "withdrawn" for row in rows),
        not_submitted_count=sum(row.choice is None and row.public_is_alive for row in rows),
        decision=decision, publication_event_id=vote.publication_event_id,
        publication_deleted=bool(publication_revision and publication_revision.deleted_at),
    )


async def current_round_output(db: AsyncSession, room: Room, vote: VoteRound,
                               user_id: UUID) -> VoteRoundOutput:
    is_host = room.owner_user_id == user_id
    ballot = None if is_host else await db.scalar(select(VoteBallot).where(
        VoteBallot.vote_round_id == vote.id, VoteBallot.voter_user_id == user_id))
    state = None if is_host else await db.get(PlayerState, (room.id, user_id))
    count = await db.scalar(select(func.count()).select_from(VoteBallot).where(
        VoteBallot.vote_round_id == vote.id,
        VoteBallot.choice.in_(("target", "skip")))) if is_host else None
    return VoteRoundOutput(
        id=vote.id, day_number=vote.day_number, status=vote.status,
        opened_at=vote.opened_at, closed_at=vote.closed_at,
        eligible=bool(not is_host and state and state.public_is_alive
                      and room.status == "playing" and not room.is_paused
                      and room.phase_type == "day" and room.round_number == vote.day_number
                      and vote.status == "open"),
        my_choice=ballot.choice if ballot else None,
        my_target_user_id=ballot.target_user_id if ballot else None,
        submitted_count=count,
    )


@router.get("/latest", response_model=VoteRoundOutput | None)
async def latest_vote(room_id: UUID, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> VoteRoundOutput | None:
    await require_room_access(db, room_id, user.id)
    room = await db.get(Room, room_id)
    vote = await db.scalar(select(VoteRound).where(VoteRound.room_id == room_id)
                           .order_by(VoteRound.opened_at.desc(), VoteRound.id.desc()).limit(1))
    return await current_round_output(db, room, vote, user.id) if vote else None


@router.get("/rounds", response_model=list[VoteHostRoundOutput])
async def host_vote_rounds(room_id: UUID, user: User = Depends(current_user),
                           db: AsyncSession = Depends(get_db)) -> list[VoteHostRoundOutput]:
    if not await require_room_access(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="Chỉ quản trò được xem các vòng vote")
    rounds = (await db.scalars(select(VoteRound).where(VoteRound.room_id == room_id)
                               .order_by(VoteRound.opened_at.desc(), VoteRound.id.desc()))).all()
    if not rounds:
        return []
    counts = dict((await db.execute(select(VoteBallot.vote_round_id, func.count())
        .where(VoteBallot.vote_round_id.in_([row.id for row in rounds]),
               VoteBallot.choice.in_(("target", "skip")))
        .group_by(VoteBallot.vote_round_id))).all())
    return [VoteHostRoundOutput(
        id=row.id, day_number=row.day_number, status=row.status,
        opened_at=row.opened_at, closed_at=row.closed_at,
        eligible=False, my_choice=None, my_target_user_id=None,
        submitted_count=counts.get(row.id, 0),
        decided_at=row.decided_at, published_at=row.published_at,
    ) for row in rounds]


@router.get("/{vote_id}/review", response_model=VoteReviewOutput)
async def review_vote(room_id: UUID, vote_id: UUID,
                      user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> VoteReviewOutput:
    if not await require_room_access(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="Chỉ quản trò được xem phiếu")
    vote = await db.get(VoteRound, vote_id)
    if vote is None or vote.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy vòng vote")
    return await review_for_host(db, room_id, vote)


@router.get("/publications", response_model=list[PublicVoteNotice])
async def public_vote_notices(room_id: UUID, user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_db)) -> list[PublicVoteNotice]:
    await require_room_access(db, room_id, user.id)
    events = (await db.scalars(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.visibility == "room",
        GameEvent.kind == "vote.result_announced",
    ).order_by(GameEvent.sequence))).all()
    revisions = {row.source_event_id: row for row in (await db.scalars(
        select(ContentRevision).where(ContentRevision.room_id == room_id,
                                      ContentRevision.source_event_id.in_([event.id for event in events]))
    )).all()} if events else {}
    return [PublicVoteNotice(
        id=event.id, vote_round_id=UUID(event.payload["vote_round_id"]),
        day_number=event.payload["day_number"],
        text=(revisions[event.id].current_text if event.id in revisions and revisions[event.id].edited_at
              else event.payload["text"]),
        created_at=event.created_at,
        edited_at=revisions[event.id].edited_at if event.id in revisions else None,
    ) for event in events if event.id not in revisions or revisions[event.id].deleted_at is None]


@router.post("", response_model=VoteMutationOutput)
async def open_vote(room_id: UUID, body: VoteCommand, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)) -> VoteMutationOutput:
    room = await locked_host_room(db, room_id, user.id)
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "vote.opened":
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return VoteMutationOutput(event_id=prior.id,
                                  vote_round_id=UUID(prior.payload["vote_round_id"]), repeated=True)
    if room.status != "playing" or room.phase_type != "day" or room.is_paused:
        raise HTTPException(status_code=409, detail="Chỉ mở vote trong pha ngày đang diễn ra")
    if await db.scalar(select(VoteRound.id).where(
        VoteRound.room_id == room_id, VoteRound.status == "open")) is not None:
        raise HTTPException(status_code=409, detail="Vòng vote khác vẫn đang mở")
    vote = VoteRound(room_id=room_id, day_number=room.round_number, status="open")
    db.add(vote)
    await db.flush()
    event = await append_event(db, room_id, user.id, "vote.opened", "room",
                               {"vote_round_id": str(vote.id), "day_number": vote.day_number},
                               client_request_id=body.client_request_id)
    await db.commit()
    return VoteMutationOutput(event_id=event.id, vote_round_id=vote.id, repeated=False)


@router.post("/{vote_id}/close", response_model=VoteMutationOutput)
async def close_vote(room_id: UUID, vote_id: UUID, body: VoteCommand,
                     user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)) -> VoteMutationOutput:
    room = await locked_host_room(db, room_id, user.id)
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "vote.closed" or prior.payload.get("vote_round_id") != str(vote_id):
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return VoteMutationOutput(event_id=prior.id, vote_round_id=vote_id, repeated=True)
    vote = await db.get(VoteRound, vote_id)
    if vote is None or vote.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy vòng vote")
    if room.status != "playing" or vote.status != "open":
        raise HTTPException(status_code=409, detail="Vòng vote đã đóng hoặc trận đã kết thúc")
    vote.status = "closed"
    vote.closed_at = now_utc()
    event = await append_event(db, room_id, user.id, "vote.closed", "room",
                               {"vote_round_id": str(vote.id), "day_number": vote.day_number},
                               client_request_id=body.client_request_id)
    await db.commit()
    return VoteMutationOutput(event_id=event.id, vote_round_id=vote.id, repeated=False)


@router.post("/{vote_id}/ballots", response_model=VoteMutationOutput)
async def submit_ballot(room_id: UUID, vote_id: UUID, body: BallotInput,
                        user: User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)) -> VoteMutationOutput:
    if await require_room_access(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="Quản trò không bỏ phiếu")
    room = (await db.scalars(select(Room).where(Room.id == room_id).with_for_update())).one()
    request = {"vote_round_id": str(vote_id), "choice": body.choice,
               "target_user_id": str(body.target_user_id) if body.target_user_id else None}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "vote.ballot_submitted" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return VoteMutationOutput(event_id=prior.id, vote_round_id=vote_id, repeated=True)
    vote = await db.get(VoteRound, vote_id)
    if vote is None or vote.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy vòng vote")
    if (room.status != "playing" or room.is_paused or room.phase_type != "day"
            or room.round_number != vote.day_number or vote.status != "open"):
        raise HTTPException(status_code=409, detail="Vòng vote không còn nhận phiếu")
    voter = await db.get(PlayerState, (room_id, user.id))
    if voter is None or not voter.public_is_alive:
        raise HTTPException(status_code=403, detail="Ghế này không được bỏ phiếu")
    if body.target_user_id is not None:
        target_member = await db.get(RoomMember, (room_id, body.target_user_id))
        target = await db.get(PlayerState, (room_id, body.target_user_id))
        if target_member is None or target_member.left_at is not None or target is None or not target.public_is_alive:
            raise HTTPException(status_code=422, detail="Chỉ chọn người đang được công bố là sống")
    ballot = await db.scalar(select(VoteBallot).where(
        VoteBallot.vote_round_id == vote_id, VoteBallot.voter_user_id == user.id))
    previous = {"choice": ballot.choice, "target_user_id": str(ballot.target_user_id)
                if ballot.target_user_id else None} if ballot else None
    new_choice = "withdrawn" if body.choice == "withdraw" else body.choice
    if body.choice == "withdraw" and (ballot is None or ballot.choice == "withdrawn"):
        raise HTTPException(status_code=409, detail="Chưa có phiếu để rút")
    if previous == {"choice": new_choice, "target_user_id": request["target_user_id"]}:
        raise HTTPException(status_code=409, detail="Phiếu chưa thay đổi")
    if ballot is None:
        ballot = VoteBallot(vote_round_id=vote_id, voter_user_id=user.id,
                            choice=new_choice, target_user_id=body.target_user_id,
                            updated_at=now_utc())
        db.add(ballot)
    else:
        ballot.choice = new_choice
        ballot.target_user_id = body.target_user_id
        ballot.updated_at = now_utc()
    event = await append_event(db, room_id, user.id, "vote.ballot_submitted", "host",
                               {"request": request, "previous": previous,
                                "current": {"choice": new_choice,
                                            "target_user_id": request["target_user_id"]}},
                               client_request_id=body.client_request_id)
    await db.commit()
    return VoteMutationOutput(event_id=event.id, vote_round_id=vote_id, repeated=False)


@router.post("/{vote_id}/decision", response_model=VoteMutationOutput)
async def decide_vote(room_id: UUID, vote_id: UUID, body: VoteDecisionInput,
                      user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> VoteMutationOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"vote_round_id": str(vote_id), "decision_text": body.decision_text,
               "reason": body.reason,
               "target_user_id": str(body.target_user_id) if body.target_user_id else None}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "vote.decided" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return VoteMutationOutput(event_id=prior.id, vote_round_id=vote_id, repeated=True)
    vote = await db.get(VoteRound, vote_id)
    if vote is None or vote.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy vòng vote")
    if room.status != "playing" or vote.status != "closed":
        raise HTTPException(status_code=409, detail="Đóng vòng vote trước khi quyết định")
    if vote.decided_at is not None:
        raise HTTPException(status_code=409, detail="Vòng vote đã có quyết định")
    if body.target_user_id is not None and await db.get(
        RoomMember, (room_id, body.target_user_id)) is None:
        raise HTTPException(status_code=422, detail="Người liên quan không thuộc phòng")
    review = await review_for_host(db, room_id, vote)
    event = await append_event(db, room_id, user.id, "vote.decided", "host",
                               {"request": request, "day_number": vote.day_number,
                                "tally": [item.model_dump(mode="json") for item in review.tally],
                                "skip_count": review.skip_count,
                                "withdrawn_count": review.withdrawn_count,
                                "not_submitted_count": review.not_submitted_count},
                               client_request_id=body.client_request_id)
    vote.decision_text = body.decision_text
    vote.decision_reason = body.reason
    vote.decision_target_user_id = body.target_user_id
    vote.decision_event_id = event.id
    vote.decided_at = now_utc()
    await db.commit()
    return VoteMutationOutput(event_id=event.id, vote_round_id=vote_id, repeated=False)


@router.post("/{vote_id}/publication", response_model=VotePublicationOutput)
async def publish_vote_result(room_id: UUID, vote_id: UUID, body: VotePublicationInput,
                              user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_db)) -> VotePublicationOutput:
    room = await locked_host_room(db, room_id, user.id)
    request = {"vote_round_id": str(vote_id), "include_result": body.include_result,
               "include_voter_names": body.include_voter_names,
               "include_reason": body.include_reason}
    prior = await db.scalar(select(GameEvent).where(
        GameEvent.room_id == room_id, GameEvent.client_request_id == body.client_request_id))
    if prior is not None:
        if prior.kind != "vote.publication_decided" or prior.payload.get("request") != request:
            raise HTTPException(status_code=409, detail="Mã yêu cầu đã dùng cho thao tác khác")
        return VotePublicationOutput(event_id=prior.id,
                                     public_event_id=UUID(prior.payload["public_event_id"]),
                                     vote_round_id=vote_id, repeated=True)
    vote = await db.get(VoteRound, vote_id)
    if vote is None or vote.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy vòng vote")
    if room.status != "playing" or vote.decided_at is None:
        raise HTTPException(status_code=409, detail="Cần chốt vote trước khi công bố")
    if vote.publication_event_id is not None:
        raise HTTPException(status_code=409, detail="Kết quả vote đã được công bố")
    parts = [f"Vote Ngày {vote.day_number}"]
    if body.include_result:
        parts.append(f"Kết quả: {vote.decision_text}")
    if body.include_voter_names:
        review = await review_for_host(db, room_id, vote)
        voters = [f"ghế {row.seat_number} · {row.display_name}" for row in review.ballots
                  if row.choice in ("target", "skip")]
        parts.append("Đã gửi phiếu: " + (", ".join(voters) if voters else "không có ai"))
    if body.include_reason:
        parts.append(f"Lý do: {vote.decision_reason}")
    public_text = "\n".join(parts)
    decision = await append_event(db, room_id, user.id, "vote.publication_decided", "host",
                                  {"request": request}, client_request_id=body.client_request_id)
    published = await append_event(db, room_id, user.id, "vote.result_announced", "room",
                                   {"vote_round_id": str(vote_id), "day_number": vote.day_number,
                                    "text": public_text})
    decision.payload = {**decision.payload, "public_event_id": str(published.id)}
    vote.publication_event_id = published.id
    vote.published_at = now_utc()
    await db.commit()
    return VotePublicationOutput(event_id=decision.id, public_event_id=published.id,
                                 vote_round_id=vote_id, repeated=False)
