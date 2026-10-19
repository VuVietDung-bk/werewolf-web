import asyncio
from datetime import timedelta
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.auth import COOKIE_NAME, POSTGAME_POLICY_VERSION, now_utc, secure_cookie, token_hash
from app.db.events import append_event
from app.db.models import GameEvent, Outbox, Room, RoomMember, Session, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


def test_secure_cookie_is_default(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    assert secure_cookie()
    monkeypatch.setenv("APP_ENV", "local")
    assert not secure_cookie()


async def websocket_messages(cookie: str | None, origin: str | None = ORIGIN) -> list[dict]:
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", f"{COOKIE_NAME}={cookie}".encode()))
    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    await app({"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
               "path": "/api/ws/session", "raw_path": b"/api/ws/session",
               "query_string": b"", "headers": headers, "client": ("test", 1234),
               "server": ("test", 80), "subprotocols": []}, receive, send)
    return messages


def test_guest_session_room_guard_and_event_atomicity() -> None:
    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        base = "http://test"
        async with (
            httpx.AsyncClient(transport=transport, base_url=base) as host,
            httpx.AsyncClient(transport=transport, base_url=base) as member,
            httpx.AsyncClient(transport=transport, base_url=base) as outsider,
        ):
            assert (await host.get("/api/auth/me")).status_code == 401
            assert (await host.post("/api/auth/guest", json={"display_name": "Host"})).status_code == 403
            host_created = await host.post(
                "/api/auth/guest", json={"display_name": " Host "}, headers={"Origin": ORIGIN}
            )
            assert host_created.status_code == 200
            host_id = host_created.json()["id"]
            cookie = host.cookies.get(COOKIE_NAME)
            assert cookie and len(cookie) > 30
            assert "HttpOnly" in host_created.headers["set-cookie"]
            assert "SameSite=lax" in host_created.headers["set-cookie"]
            assert (await host.get("/api/auth/me")).json()["id"] == host_id
            assert (await host.post("/api/auth/guest", json={"display_name": "Other"},
                                    headers={"Origin": ORIGIN})).json()["id"] == host_id

            member_id = (await member.post("/api/auth/guest", json={"display_name": "Member"},
                                           headers={"Origin": ORIGIN})).json()["id"]
            outsider_id = (await outsider.post("/api/auth/guest", json={"display_name": "Outsider"},
                                               headers={"Origin": ORIGIN})).json()["id"]
            room_id = uuid4()
            async with session_factory()() as db:
                db.add(Room(id=room_id, owner_user_id=host_id, code=uuid4().hex[:12],
                            name="Test room", seat_limit=25))
                db.add(RoomMember(room_id=room_id, user_id=member_id, seat_number=1,
                                  consent_version=POSTGAME_POLICY_VERSION, consented_at=now_utc()))
                await db.commit()
            assert (await host.get(f"/api/rooms/{room_id}/access")).json() == {"is_host": True}
            assert (await member.get(f"/api/rooms/{room_id}/access")).json() == {"is_host": False}
            denied = await outsider.get(f"/api/rooms/{room_id}/access",
                                        headers={"X-Role": "host", "X-Team": "wolves"})
            assert denied.status_code == 403
            assert (await websocket_messages(cookie))[0]["type"] == "websocket.accept"
            assert (await websocket_messages(cookie, "https://evil.example"))[0]["code"] == 4403
            assert (await websocket_messages(None))[0]["code"] == 4401

            try:
                async with session_factory()() as db:
                    async with db.begin():
                        await append_event(db, room_id, host_id, "test.rollback", "host", {"n": 1})
                        raise RuntimeError("force rollback")
            except RuntimeError:
                pass
            async with session_factory()() as db:
                assert (await db.scalar(select(Room.version).where(Room.id == room_id))) == 0
                assert (await db.scalar(select(func.count()).select_from(GameEvent)
                                        .where(GameEvent.room_id == room_id))) == 0
                assert (await db.scalar(select(func.count()).select_from(Outbox)
                                        .join(GameEvent, Outbox.event_id == GameEvent.id)
                                        .where(GameEvent.room_id == room_id))) == 0
            async with session_factory()() as db:
                async with db.begin():
                    event = await append_event(db, room_id, host_id, "test.commit", "host",
                                               {"n": 2}, uuid4())
                    assert event.sequence == 1
            async with session_factory()() as db:
                assert (await db.scalar(select(Room.version).where(Room.id == room_id))) == 1
                assert (await db.scalar(select(func.count()).select_from(GameEvent)
                                        .where(GameEvent.room_id == room_id))) == 1
                assert (await db.scalar(select(func.count()).select_from(Outbox)
                                        .join(GameEvent, Outbox.event_id == GameEvent.id)
                                        .where(GameEvent.room_id == room_id))) == 1

            async def write_concurrent(number: int) -> None:
                async with session_factory()() as db:
                    async with db.begin():
                        await append_event(db, room_id, host_id, "test.concurrent", "host", {"n": number})

            await asyncio.gather(write_concurrent(3), write_concurrent(4))
            async with session_factory()() as db:
                sequences = (await db.scalars(select(GameEvent.sequence).where(
                    GameEvent.room_id == room_id).order_by(GameEvent.sequence))).all()
                assert sequences == [1, 2, 3]
                assert (await db.scalar(select(func.count()).select_from(Outbox)
                                        .join(GameEvent, Outbox.event_id == GameEvent.id)
                                        .where(GameEvent.room_id == room_id))) == 3

            assert (await host.post("/api/auth/logout", headers={"Origin": ORIGIN})).status_code == 204
            assert (await host.get("/api/auth/me")).status_code == 401
            assert (await websocket_messages(cookie))[0]["code"] == 4401
            member_cookie = member.cookies.get(COOKIE_NAME)
            async with session_factory()() as db:
                session = await db.scalar(select(Session).where(Session.token_hash == token_hash(member_cookie)))
                session.expires_at = now_utc() - timedelta(seconds=1)
                await db.commit()
            assert (await member.get("/api/auth/me")).status_code == 401
            assert (await websocket_messages(member_cookie))[0]["code"] == 4401
            assert (await outsider.get("/api/auth/me")).json()["id"] == outsider_id

            async with session_factory()() as db:
                await db.delete(await db.get(Room, room_id))
                await db.commit()
                for id_ in (host_id, member_id, outsider_id):
                    await db.delete(await db.get(User, id_))
                await db.commit()

    asyncio.run(run())
