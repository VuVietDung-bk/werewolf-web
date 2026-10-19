import asyncio
from uuid import UUID

import httpx
from sqlalchemy import func, select

from app.auth import COOKIE_NAME
from app.db.models import GameEvent, Outbox, Room, RoomMember, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def guest(client: httpx.AsyncClient, name: str) -> str:
    response = await client.post("/api/auth/guest", json={"display_name": name},
                                 headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return response.json()["id"]


def test_room_lifecycle_permissions_and_last_seat_race() -> None:
    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as host,
            httpx.AsyncClient(transport=transport, base_url="http://test") as first,
            httpx.AsyncClient(transport=transport, base_url="http://test") as second,
            httpx.AsyncClient(transport=transport, base_url="http://test") as third,
            httpx.AsyncClient(transport=transport, base_url="http://test") as outsider,
        ):
            ids = [await guest(client, name) for client, name in (
                (host, "Host"), (first, "First"), (second, "Second"),
                (third, "Third"), (outsider, "Outsider"))]
            invalid = await host.post("/api/rooms", json={"name": "X", "seat_limit": 31},
                                      headers={"Origin": ORIGIN})
            assert invalid.status_code == 422
            created = await host.post("/api/rooms", json={"name": "  Thôn Sương  ", "seat_limit": 2},
                                      headers={"Origin": ORIGIN})
            assert created.status_code == 201
            room = created.json()
            room_id, code = room["id"], room["code"]
            assert room["name"] == "Thôn Sương" and room["members"] == []
            assert room["is_host"] and room["version"] == 1
            assert len(code) == 12
            assert (await outsider.get(f"/api/rooms/{room_id}")).status_code == 403
            assert (await outsider.get("/api/rooms/mine")).json() == []
            assert (await first.post("/api/rooms/join", json={"code": "AAAAAAAAAAAA",
                "accept_postgame_transcript": True}, headers={"Origin": ORIGIN})).status_code == 404
            assert (await first.post("/api/rooms/join", json={"code": code,
                "accept_postgame_transcript": False}, headers={"Origin": ORIGIN})).status_code == 422
            assert (await host.post("/api/rooms/join", json={"code": code,
                "accept_postgame_transcript": True}, headers={"Origin": ORIGIN})).status_code == 403
            join_body = {"code": code.lower(), "accept_postgame_transcript": True}
            joined = await first.post("/api/rooms/join", json=join_body, headers={"Origin": ORIGIN})
            assert joined.status_code == 200
            assert joined.json()["members"][0]["seat_number"] == 1
            repeat = await first.post("/api/rooms/join", json=join_body, headers={"Origin": ORIGIN})
            assert repeat.status_code == 200 and repeat.json()["version"] == joined.json()["version"]
            assert (await first.patch(f"/api/rooms/{room_id}/settings", json={
                "name": "Changed", "seat_limit": 2, "registration_open": False
            }, headers={"Origin": ORIGIN})).status_code == 403
            assert (await host.get(f"/api/rooms/{room_id}")).json()["version"] == joined.json()["version"]
            closed = await host.patch(f"/api/rooms/{room_id}/settings", json={
                "name": "Thôn Sương", "seat_limit": 2, "registration_open": False
            }, headers={"Origin": ORIGIN})
            assert closed.status_code == 200 and closed.json()["status"] == "closed"
            assert (await second.post("/api/rooms/join", json=join_body,
                                      headers={"Origin": ORIGIN})).status_code == 409
            opened = await host.patch(f"/api/rooms/{room_id}/settings", json={
                "name": "Thôn Sương", "seat_limit": 2, "registration_open": True
            }, headers={"Origin": ORIGIN})
            assert opened.status_code == 200 and opened.json()["status"] == "open"

            responses = await asyncio.gather(*(
                client.post("/api/rooms/join", json=join_body, headers={"Origin": ORIGIN})
                for client in (second, third)
            ))
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner = second if responses[0].status_code == 200 else third
            loser = third if winner is second else second
            full = await host.get(f"/api/rooms/{room_id}")
            assert [member["seat_number"] for member in full.json()["members"]] == [1, 2]
            assert (await loser.get(f"/api/rooms/{room_id}")).status_code == 403
            assert (await host.patch(f"/api/rooms/{room_id}/settings", json={
                "name": "Thôn Sương", "seat_limit": 1, "registration_open": True
            }, headers={"Origin": ORIGIN})).status_code == 422
            assert (await host.post(f"/api/rooms/{room_id}/leave",
                                    headers={"Origin": ORIGIN})).status_code == 403
            assert (await first.post(f"/api/rooms/{room_id}/leave",
                                     headers={"Origin": ORIGIN})).status_code == 204
            assert (await first.get(f"/api/rooms/{room_id}")).status_code == 403
            reused = await loser.post("/api/rooms/join", json=join_body, headers={"Origin": ORIGIN})
            assert reused.status_code == 200
            assert [member["seat_number"] for member in reused.json()["members"]] == [1, 2]
            assert (await first.get("/api/rooms/mine")).json() == []
            assert len((await host.get("/api/rooms/mine")).json()) == 1
            assert len((await winner.get("/api/rooms/mine")).json()) == 1

            # A fresh client process using the same browser cookie sees the same seat.
            async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                         cookies={COOKIE_NAME: loser.cookies.get(COOKIE_NAME)}) as restored:
                assert (await restored.get("/api/auth/me")).status_code == 200
                restored_room = await restored.get(f"/api/rooms/{room_id}")
                assert restored_room.status_code == 200
                assert any(member["user_id"] == ids[2 if loser is second else 3]
                           and member["seat_number"] == 1 for member in restored_room.json()["members"])

            async with session_factory()() as db:
                events = (await db.scalars(select(GameEvent).where(GameEvent.room_id == UUID(room_id))
                                           .order_by(GameEvent.sequence))).all()
                assert [event.sequence for event in events] == list(range(1, len(events) + 1))
                assert (await db.scalar(select(func.count()).select_from(Outbox)
                                        .where(Outbox.event_id.in_([event.id for event in events])))) == len(events)
                historical = (await db.scalars(select(RoomMember).where(
                    RoomMember.room_id == UUID(room_id), RoomMember.user_id == UUID(ids[1])))).one()
                assert historical.left_at is not None and historical.seat_number == 1
                assert (await db.scalar(select(Room.version).where(Room.id == UUID(room_id)))) == len(events)
                await db.delete(await db.get(Room, UUID(room_id)))
                await db.commit()
                for user_id in ids:
                    await db.delete(await db.get(User, UUID(user_id)))
                await db.commit()

    asyncio.run(run())
