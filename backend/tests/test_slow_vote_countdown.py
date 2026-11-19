"""Slow chat and timed votes keep their limits at the server boundary."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app import chat, votes
from app.auth import now_utc
from app.db.models import GameEvent, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_slow_chat_and_vote_countdown(monkeypatch):
    async def run():
        ids = []
        room_id = None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as host, \
                   httpx.AsyncClient(transport=transport, base_url="http://test") as player, \
                   httpx.AsyncClient(transport=transport, base_url="http://test") as other:
            try:
                for client, name in ((host, "Quản trò giờ"), (player, "Người chơi giờ"), (other, "Người chơi khác")):
                    result = await post(client, "/api/auth/guest", {"display_name": name})
                    assert result.status_code == 200
                    ids.append(result.json()["id"])
                created = (await post(host, "/api/rooms", {"name": "Phòng đồng hồ", "seat_limit": 2})).json()
                room_id = created["id"]
                base = f"/api/rooms/{room_id}"
                for member in (player, other):
                    assert (await post(member, "/api/rooms/join", {
                        "code": created["code"], "accept_postgame_transcript": True})).status_code == 200

                policy = {"client_request_id": str(uuid4()), "common_send_at_night": False,
                          "team_send_in_day": False, "dead_common_send": False,
                          "dead_team_send": False, "slow_mode_seconds": 2}
                assert (await host.patch(f"{base}/chat/policy", json={**policy, "slow_mode_seconds": 1},
                                         headers={"Origin": ORIGIN})).status_code == 422
                assert (await host.patch(f"{base}/chat/policy", json=policy,
                                         headers={"Origin": ORIGIN})).status_code == 200
                chat_url = f"{base}/chat/messages"
                first = {"client_request_id": str(uuid4()), "channel": "common", "text": "Một"}
                assert (await post(player, chat_url, first)).status_code == 200
                assert (await post(player, chat_url, first)).json()["repeated"] is True
                denied = await post(player, chat_url, {**first, "client_request_id": str(uuid4()), "text": "Hai"})
                assert denied.status_code == 429 and int(denied.headers["Retry-After"]) >= 1
                assert (await post(other, chat_url, {"client_request_id": str(uuid4()),
                    "channel": "common", "text": "Người khác vẫn gửi"})).status_code == 200
                assert (await post(host, chat_url, {"client_request_id": str(uuid4()),
                    "channel": "common", "text": "Quản trò vẫn gửi"})).status_code == 200
                context = (await player.get(f"{base}/chat/context")).json()
                assert context["policy"]["slow_mode_seconds"] == 2 and context["next_chat_at"]
                monkeypatch.setattr(chat, "now_utc", lambda: now_utc() + timedelta(seconds=3))
                assert (await post(player, chat_url, {"client_request_id": str(uuid4()),
                    "channel": "common", "text": "Hai"})).status_code == 200
                monkeypatch.undo()

                role = {"name": "Dân", "description": "Thử vote", "faction_label": "Làng",
                        "seer_result": "Thiện", "role_kind": "passive", "quantity": 2}
                assert (await post(host, f"{base}/roles", role)).status_code == 201
                assert (await host.patch(f"{base}/settings", json={"name": "Phòng đồng hồ",
                    "seat_limit": 2, "registration_open": False}, headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200
                votes_url = f"{base}/votes"
                assert (await post(host, votes_url, {"client_request_id": str(uuid4()),
                    "countdown_seconds": 9})).status_code == 422
                opened = await post(host, votes_url, {"client_request_id": str(uuid4()),
                                                       "countdown_seconds": 10})
                assert opened.status_code == 200
                vote_id = opened.json()["vote_round_id"]
                latest = (await player.get(f"{votes_url}/latest")).json()
                assert latest["status"] == "open" and latest["closes_at"] and latest["eligible"]
                ballot = {"client_request_id": str(uuid4()), "choice": "skip"}
                assert (await post(player, f"{votes_url}/{vote_id}/ballots", ballot)).status_code == 200
                monkeypatch.setattr(votes, "now_utc", lambda: now_utc() + timedelta(seconds=11))
                late = await post(player, f"{votes_url}/{vote_id}/ballots", {
                    "client_request_id": str(uuid4()), "choice": "withdraw"})
                assert late.status_code == 409
                closed = (await host.get(f"{votes_url}/latest")).json()
                assert closed["status"] == "closed" and closed["closed_at"] == closed["closes_at"]
                assert (await post(player, f"{votes_url}/{vote_id}/ballots", ballot)).json()["repeated"]
                async with session_factory()() as db:
                    events = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind == "vote.closed"))).all()
                    assert len(events) == 1 and events[0].payload["automatic"] is True
                second_round = await post(host, votes_url, {"client_request_id": str(uuid4()),
                                                            "countdown_seconds": 10})
                assert second_round.status_code == 200
                monkeypatch.setattr(votes, "now_utc", lambda: now_utc() + timedelta(seconds=22))
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                rounds = (await host.get(f"{votes_url}/rounds")).json()
                assert rounds[0]["status"] == "closed"
            finally:
                monkeypatch.undo()
                async with session_factory()() as db:
                    if room_id:
                        room = await db.get(Room, UUID(room_id))
                        if room:
                            await db.delete(room)
                            await db.commit()
                    for user_id in ids:
                        user = await db.get(User, UUID(user_id))
                        if user:
                            await db.delete(user)
                    await db.commit()

    asyncio.run(run())
