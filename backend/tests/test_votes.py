"""Daytime vote permissions, privacy, phase boundaries, and manual control."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

import app.votes as vote_module
from app.db.models import GameEvent, Outbox, Room, User, VoteBallot, VoteRound
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_vote_round_and_ballot_permissions() -> None:
    async def run() -> None:
        user_ids: list[str] = []
        room_id: str | None = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, a, b, c, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(5)]
            try:
                for client, name in zip((host, a, b, c, outsider),
                                        ("Quản trò vote", "Người A", "Người B", "Người C", "Ngoài phòng")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Vote thủ công", "seat_limit": 3})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                votes = f"{base}/votes"
                for client in (a, b, c):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{base}/roles", {
                    "name": "Dân", "description": "Role thử vote", "faction_label": "Làng",
                    "seer_result": "Thiện", "role_kind": "passive", "quantity": 3})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Vote thủ công", "seat_limit": 3, "registration_open": False,
                }, headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200
                assert (await outsider.get(f"{votes}/latest")).status_code == 403
                assert (await a.get(f"{votes}/latest")).json() is None
                assert (await host.get(f"{votes}/rounds")).json() == []
                assert (await a.get(f"{votes}/rounds")).status_code == 403
                assert (await outsider.get(f"{votes}/rounds")).status_code == 403
                assert (await post(a, votes, {"client_request_id": str(uuid4())})).status_code == 403

                # A secret death must not change public vote eligibility or reveal that death.
                assert (await post(host, f"{base}/game/players/{user_ids[3]}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill", "reason": "Kín"})).status_code == 200
                open_body = {"client_request_id": str(uuid4())}
                opened = await post(host, votes, open_body)
                assert opened.status_code == 200 and not opened.json()["repeated"]
                vote_id = opened.json()["vote_round_id"]
                first_rounds = (await host.get(f"{votes}/rounds")).json()
                assert len(first_rounds) == 1 and first_rounds[0]["id"] == vote_id
                assert first_rounds[0]["status"] == "open" and first_rounds[0]["submitted_count"] == 0
                decision_url = f"{votes}/{vote_id}/decision"
                assert (await post(host, decision_url, {"client_request_id": str(uuid4()),
                    "decision_text": "Chưa đóng vòng", "reason": "Thử quyền"})).status_code == 409
                assert (await post(host, votes, open_body)).json()["repeated"]
                assert (await post(host, votes, {"client_request_id": str(uuid4())})).status_code == 409
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 409
                host_view = (await host.get(f"{votes}/latest")).json()
                assert host_view["day_number"] == 0 and host_view["submitted_count"] == 0
                assert host_view["eligible"] is False and host_view["my_choice"] is None
                public_version_before_ballots = (await b.get(base)).json()["version"]
                c_view = (await c.get(f"{votes}/latest")).json()
                assert c_view["eligible"] is True and c_view["submitted_count"] is None

                ballot_url = f"{votes}/{vote_id}/ballots"
                target_a = {"client_request_id": str(uuid4()), "choice": "target",
                            "target_user_id": user_ids[2]}
                assert (await post(host, ballot_url, target_a)).status_code == 403
                assert (await post(outsider, ballot_url, target_a)).status_code == 403
                assert (await post(a, ballot_url, {"client_request_id": str(uuid4()),
                    "choice": "withdraw"})).status_code == 409
                cast = await post(a, ballot_url, target_a)
                assert cast.status_code == 200 and not cast.json()["repeated"]
                assert (await post(a, ballot_url, target_a)).json()["repeated"]
                assert (await post(a, ballot_url, {**target_a, "target_user_id": user_ids[3]})).status_code == 409
                assert (await a.get(f"{votes}/latest")).json()["my_target_user_id"] == user_ids[2]
                assert (await b.get(f"{votes}/latest")).json()["my_choice"] is None
                assert (await b.get(f"{votes}/latest")).json()["submitted_count"] is None
                assert (await host.get(f"{votes}/latest")).json()["submitted_count"] == 1
                assert (await b.get(base)).json()["version"] == public_version_before_ballots

                skip = await post(b, ballot_url, {"client_request_id": str(uuid4()),
                    "choice": "skip"})
                assert skip.status_code == 200
                assert (await post(c, ballot_url, {"client_request_id": str(uuid4()),
                    "choice": "target", "target_user_id": user_ids[1]})).status_code == 200
                assert (await host.get(f"{votes}/latest")).json()["submitted_count"] == 3
                assert (await post(a, ballot_url, {"client_request_id": str(uuid4()),
                    "choice": "withdraw"})).status_code == 200
                assert (await a.get(f"{votes}/latest")).json()["my_choice"] == "withdrawn"
                assert (await host.get(f"{votes}/latest")).json()["submitted_count"] == 2
                # No quorum: the host can close even with withdrawn or missing ballots.
                close_body = {"client_request_id": str(uuid4())}
                close_url = f"{votes}/{vote_id}/close"
                assert (await post(a, close_url, close_body)).status_code == 403
                closed = await post(host, close_url, close_body)
                assert closed.status_code == 200
                assert (await post(host, close_url, close_body)).json()["repeated"]
                review_url = f"{votes}/{vote_id}/review"
                assert (await a.get(review_url)).status_code == 403
                assert (await outsider.get(review_url)).status_code == 403
                review = (await host.get(review_url)).json()
                assert [(item["voter_user_id"], item["choice"]) for item in review["ballots"]] == [
                    (user_ids[1], "withdrawn"), (user_ids[2], "skip"), (user_ids[3], "target")]
                assert review["tally"] == [{"target_user_id": user_ids[1],
                    "display_name": "Người A", "seat_number": 1, "count": 1}]
                assert (review["skip_count"], review["withdrawn_count"],
                        review["not_submitted_count"]) == (1, 1, 0)
                assert review["decision"] is None
                decision_body = {"client_request_id": str(uuid4()),
                    "decision_text": "Chọn ghế B dù phiếu nhiều nhất thuộc A",
                    "reason": "Quản trò xét tác động role", "target_user_id": user_ids[2]}
                assert (await post(a, decision_url, decision_body)).status_code == 403
                decided = await post(host, decision_url, decision_body)
                assert decided.status_code == 200 and not decided.json()["repeated"]
                assert (await post(host, decision_url, decision_body)).json()["repeated"]
                assert (await post(host, decision_url, {**decision_body,
                    "decision_text": "Đổi nội dung"})).status_code == 409
                assert (await post(host, decision_url, {**decision_body,
                    "client_request_id": str(uuid4())})).status_code == 409
                review = (await host.get(review_url)).json()
                assert review["decision"]["decision_text"] == decision_body["decision_text"]
                assert review["decision"]["reason"] == decision_body["reason"]
                assert review["decision"]["target_user_id"] == user_ids[2]
                assert "decision" not in (await b.get(f"{votes}/latest")).json()
                assert (await b.get(base)).json()["version"] == public_version_before_ballots + 1
                board_after_vote = (await host.get(f"{base}/game/players")).json()
                assert all(item["is_alive"] for item in board_after_vote
                           if item["user_id"] in (user_ids[1], user_ids[2]))
                publications_url = f"{votes}/publications"
                assert (await b.get(publications_url)).json() == []
                assert (await outsider.get(publications_url)).status_code == 403
                publication_url = f"{votes}/{vote_id}/publication"
                assert (await post(a, publication_url, {"client_request_id": str(uuid4())})).status_code == 403
                assert (await post(host, publication_url, {"client_request_id": str(uuid4()),
                    "include_result": False, "include_voter_names": False,
                    "include_reason": False})).status_code == 422
                publication_body = {"client_request_id": str(uuid4()), "include_result": True,
                    "include_voter_names": False, "include_reason": False}
                publication = await post(host, publication_url, publication_body)
                assert publication.status_code == 200 and not publication.json()["repeated"]
                assert (await post(host, publication_url, publication_body)).json()["repeated"]
                assert (await post(host, publication_url, {**publication_body,
                    "include_reason": True})).status_code == 409
                assert (await post(host, publication_url, {**publication_body,
                    "client_request_id": str(uuid4())})).status_code == 409
                public_notice = (await b.get(publications_url)).json()[0]
                assert public_notice["id"] == publication.json()["public_event_id"]
                assert decision_body["decision_text"] in public_notice["text"]
                assert decision_body["reason"] not in public_notice["text"]
                assert "Người C" not in public_notice["text"]
                assert "decision_text" not in public_notice and "reason" not in public_notice
                assert (await host.get(review_url)).json()["publication_event_id"] == public_notice["id"]
                assert (await b.get(base)).json()["version"] == public_version_before_ballots + 2
                revision_url = f"{base}/game/content/{public_notice['id']}/revisions"
                assert (await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "edit", "text": "Kết quả đã chỉnh công khai"})).status_code == 200
                assert (await b.get(publications_url)).json()[0]["text"] == "Kết quả đã chỉnh công khai"
                assert (await post(host, revision_url, {"client_request_id": str(uuid4()),
                    "action": "delete"})).status_code == 200
                assert (await b.get(publications_url)).json() == []
                assert (await host.get(review_url)).json()["publication_deleted"] is True
                assert (await post(b, ballot_url, {"client_request_id": str(uuid4()),
                    "choice": "withdraw"})).status_code == 409
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                assert (await post(host, votes, {"client_request_id": str(uuid4())})).status_code == 409
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "night", "expected_round_number": 1})).status_code == 200

                # Public death now removes the chair from voter and target choices.
                assert (await post(host, f"{base}/game/players/{user_ids[3]}/announce", {
                    "client_request_id": str(uuid4()), "moderator_reason": "Đã công bố",
                    "reveal_role": False})).status_code == 200
                opened_day1 = await post(host, votes, {"client_request_id": str(uuid4())})
                assert opened_day1.status_code == 200
                day1_id = opened_day1.json()["vote_round_id"]
                round_history = (await host.get(f"{votes}/rounds")).json()
                assert [item["id"] for item in round_history] == [day1_id, vote_id]
                assert round_history[0]["status"] == "open"
                assert round_history[1]["decided_at"] and round_history[1]["published_at"]
                assert round_history[1]["submitted_count"] == 2
                assert (await post(host, f"{votes}/{day1_id}/publication", {
                    "client_request_id": str(uuid4())})).status_code == 409
                assert (await c.get(f"{votes}/latest")).json()["eligible"] is False
                assert (await post(c, f"{votes}/{day1_id}/ballots", {"client_request_id": str(uuid4()),
                    "choice": "skip"})).status_code == 403
                assert (await post(a, f"{votes}/{day1_id}/ballots", {"client_request_id": str(uuid4()),
                    "choice": "target", "target_user_id": user_ids[3]})).status_code == 422
                assert (await post(a, f"{votes}/{day1_id}/ballots", {"client_request_id": str(uuid4()),
                    "choice": "skip"})).status_code == 200
                assert (await post(host, f"{base}/game/finish", {
                    "result_label": "Kết thúc", "result_reason": "Còn vote mở"})).status_code == 409
                assert (await post(host, f"{base}/game/pause", {
                    "reason": "Tạm dừng"})).status_code == 200
                assert (await post(a, f"{votes}/{day1_id}/ballots", {"client_request_id": str(uuid4()),
                    "choice": "skip"})).status_code == 409
                assert (await post(host, f"{votes}/{day1_id}/close", {
                    "client_request_id": str(uuid4())})).status_code == 200
                assert (await post(host, f"{base}/game/resume")).status_code == 200
                day1_decision = await post(host, f"{votes}/{day1_id}/decision", {
                    "client_request_id": str(uuid4()), "decision_text": "Không loại ai",
                    "reason": "Thiếu phiếu", "target_user_id": None})
                assert day1_decision.status_code == 200
                day1_review = (await host.get(f"{votes}/{day1_id}/review")).json()
                assert day1_review["decision"]["target_user_id"] is None
                assert day1_review["not_submitted_count"] == 1
                day1_publication = await post(host, f"{votes}/{day1_id}/publication", {
                    "client_request_id": str(uuid4()), "include_result": False,
                    "include_voter_names": True, "include_reason": True})
                assert day1_publication.status_code == 200
                round_history = (await host.get(f"{votes}/rounds")).json()
                assert round_history[0]["status"] == "closed"
                assert round_history[0]["decided_at"] and round_history[0]["published_at"]
                public_day1 = (await c.get(publications_url)).json()[0]["text"]
                assert "Không loại ai" not in public_day1 and "Thiếu phiếu" in public_day1
                assert "Người A" in public_day1 and "Người B" not in public_day1
                assert "Người C" not in public_day1
                assert (await post(host, f"{base}/game/finish", {
                    "result_label": "Kết thúc", "result_reason": "Quản trò quyết định"})).status_code == 200

                async with session_factory()() as db:
                    rounds = (await db.scalars(select(VoteRound).where(
                        VoteRound.room_id == UUID(room_id)))).all()
                    assert len(rounds) == 2 and all(row.status == "closed" for row in rounds)
                    ballots = (await db.scalars(select(VoteBallot).where(
                        VoteBallot.vote_round_id == UUID(vote_id)))).all()
                    assert len(ballots) == 3
                    events = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind.like("vote.%")
                    ).order_by(GameEvent.sequence))).all()
                    assert [event.kind for event in events].count("vote.ballot_submitted") == 5
                    assert [event.kind for event in events].count("vote.decided") == 2
                    assert [event.kind for event in events].count("vote.result_announced") == 2
                    private_publication = next(event for event in events
                                               if event.kind == "vote.publication_decided")
                    first_public = next(event for event in events
                                        if event.id == UUID(publication.json()["public_event_id"]))
                    assert private_publication.visibility == "host"
                    assert first_public.payload == {"vote_round_id": vote_id,
                        "day_number": 0, "text": public_notice["text"]}
                    for event in events:
                        assert await db.scalar(select(Outbox).where(
                            Outbox.event_id == event.id)) is not None
                    assert all(event.visibility == "host" for event in events
                               if event.kind in ("vote.ballot_submitted", "vote.decided"))
            finally:
                if room_id:
                    async with session_factory()() as db:
                        room = await db.get(Room, UUID(room_id))
                        if room:
                            await db.delete(room)
                            await db.commit()
                async with session_factory()() as db:
                    for user_id in user_ids:
                        user = await db.get(User, UUID(user_id))
                        if user:
                            await db.delete(user)
                    await db.commit()

    asyncio.run(run())


def test_vote_events_are_atomic_and_decisions_serialize(monkeypatch) -> None:
    async def run() -> None:
        user_ids: list[str] = []
        room_id: str | None = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            host, a, b = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(3)]
            original_append = vote_module.append_event
            try:
                for client, name in zip((host, a, b), ("Quản trò nguyên tử", "Ghế A", "Ghế B")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Vote nguyên tử", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                votes = f"{base}/votes"
                for client in (a, b):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{base}/roles", {
                    "name": "Dân", "description": "Kiểm tra vote", "faction_label": "Làng",
                    "seer_result": "Thiện", "role_kind": "passive", "quantity": 2})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Vote nguyên tử", "seat_limit": 2, "registration_open": False,
                }, headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200

                async def snapshot() -> tuple:
                    async with session_factory()() as db:
                        room = await db.get(Room, UUID(room_id))
                        rounds = (await db.scalars(select(VoteRound).where(
                            VoteRound.room_id == UUID(room_id)).order_by(VoteRound.opened_at))).all()
                        ballots = (await db.scalars(select(VoteBallot).where(
                            VoteBallot.vote_round_id.in_([row.id for row in rounds])))).all() if rounds else []
                        events = (await db.scalars(select(GameEvent).where(
                            GameEvent.room_id == UUID(room_id), GameEvent.kind.like("vote.%")
                        ).order_by(GameEvent.sequence))).all()
                        outbox_ids = (await db.scalars(select(Outbox.event_id).join(
                            GameEvent, GameEvent.id == Outbox.event_id).where(
                            GameEvent.room_id == UUID(room_id), GameEvent.kind.like("vote.%")))).all()
                        return (room.version, room.public_version,
                                [(row.id, row.status, row.decision_event_id,
                                  row.publication_event_id) for row in rounds],
                                [(row.id, row.choice, row.target_user_id) for row in ballots],
                                [(event.id, event.kind, event.sequence) for event in events],
                                sorted(outbox_ids))

                async def fail_after_event(kind: str, url: str, body: dict) -> None:
                    before = await snapshot()

                    async def failing_append(*args, **kwargs):
                        event = await original_append(*args, **kwargs)
                        if args[3] == kind:
                            raise RuntimeError("Lỗi thử nghiệm sau khi đã thêm sự kiện/outbox")
                        return event

                    monkeypatch.setattr(vote_module, "append_event", failing_append)
                    try:
                        assert (await post(host, url, body)).status_code == 500
                    finally:
                        monkeypatch.setattr(vote_module, "append_event", original_append)
                    assert await snapshot() == before

                open_body = {"client_request_id": str(uuid4())}
                await fail_after_event("vote.opened", votes, open_body)
                opened = await post(host, votes, open_body)
                assert opened.status_code == 200
                vote_id = opened.json()["vote_round_id"]
                ballot_url = f"{votes}/{vote_id}/ballots"
                ballot_body = {"client_request_id": str(uuid4()), "choice": "target",
                               "target_user_id": user_ids[2]}
                before = await snapshot()

                async def failing_ballot(*args, **kwargs):
                    event = await original_append(*args, **kwargs)
                    if args[3] == "vote.ballot_submitted":
                        raise RuntimeError("Lỗi thử nghiệm sau khi ghi phiếu")
                    return event

                monkeypatch.setattr(vote_module, "append_event", failing_ballot)
                try:
                    assert (await post(a, ballot_url, ballot_body)).status_code == 500
                finally:
                    monkeypatch.setattr(vote_module, "append_event", original_append)
                assert await snapshot() == before
                assert (await post(a, ballot_url, ballot_body)).status_code == 200

                close_url = f"{votes}/{vote_id}/close"
                close_body = {"client_request_id": str(uuid4())}
                await fail_after_event("vote.closed", close_url, close_body)
                assert (await post(host, close_url, close_body)).status_code == 200
                decision_url = f"{votes}/{vote_id}/decision"
                first = {"client_request_id": str(uuid4()), "decision_text": "Không loại ai",
                         "reason": "Quản trò tự quyết định"}
                await fail_after_event("vote.decided", decision_url, first)
                second = {**first, "client_request_id": str(uuid4()),
                          "decision_text": "Loại ghế B"}
                responses = await asyncio.gather(post(host, decision_url, first),
                                                 post(host, decision_url, second))
                assert sorted(response.status_code for response in responses) == [200, 409]
                winner = first if responses[0].status_code == 200 else second
                loser = second if winner is first else first
                assert (await post(host, decision_url, winner)).json()["repeated"] is True
                assert (await post(host, decision_url, loser)).status_code == 409
                review = (await host.get(f"{votes}/{vote_id}/review")).json()
                assert review["decision"]["decision_text"] == winner["decision_text"]

                publication_url = f"{votes}/{vote_id}/publication"
                publication_body = {"client_request_id": str(uuid4()),
                                    "include_result": True, "include_voter_names": False,
                                    "include_reason": False}
                await fail_after_event("vote.result_announced", publication_url, publication_body)
                assert (await b.get(f"{votes}/publications")).json() == []
                published = await post(host, publication_url, publication_body)
                assert published.status_code == 200
                assert (await post(host, publication_url, publication_body)).json()["repeated"] is True

                async with session_factory()() as db:
                    events = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind.like("vote.%")
                    ).order_by(GameEvent.sequence))).all()
                    assert [event.kind for event in events] == [
                        "vote.opened", "vote.ballot_submitted", "vote.closed", "vote.decided",
                        "vote.publication_decided", "vote.result_announced"]
                    assert [event.sequence for event in events] == list(range(
                        events[0].sequence, events[0].sequence + len(events)))
                    for event in events:
                        assert await db.scalar(select(Outbox).where(
                            Outbox.event_id == event.id)) is not None
                    assert [event.visibility for event in events] == [
                        "room", "host", "room", "host", "host", "room"]
            finally:
                monkeypatch.setattr(vote_module, "append_event", original_append)
                if room_id:
                    async with session_factory()() as db:
                        room = await db.get(Room, UUID(room_id))
                        if room:
                            await db.delete(room)
                            await db.commit()
                async with session_factory()() as db:
                    for user_id in user_ids:
                        user = await db.get(User, UUID(user_id))
                        if user:
                            await db.delete(user)
                    await db.commit()

    asyncio.run(run())
