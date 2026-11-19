"""Scheduled public notices remain private until the next valid phase transition."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.db.models import GameEvent, Room, ScheduledAnnouncement, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_scheduled_notices_publish_on_next_transition() -> None:
    async def run() -> None:
        ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, first, second = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(3)]
            try:
                for client, name in zip((host, first, second), ("Quản trò hẹn", "Người bị giết", "Người chứng kiến")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Hẹn thông báo", "seat_limit": 2})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                events_url = f"{base}/game/events"
                status_url = f"{base}/game/players/{ids[1]}"
                for client in (first, second):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{base}/roles", {
                    "name": "Dân", "description": "Mô tả", "faction_label": "Làng",
                    "seer_result": "Thiện", "role_kind": "passive", "quantity": 2})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Hẹn thông báo", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200

                custom = {"client_request_id": str(uuid4()), "text": "Đêm xuống rồi",
                          "publish_timing": "next_phase"}
                queued_custom = await post(host, f"{events_url}/announcements", custom)
                assert queued_custom.status_code == 200
                assert (await post(host, f"{events_url}/announcements", custom)).json() == {
                    "event_id": queued_custom.json()["event_id"], "repeated": True}
                assert (await post(host, f"{events_url}/announcements", {
                    **custom, "text": "Khác"})).status_code == 409
                assert (await second.get(f"{events_url}/scheduled")).status_code == 403
                assert (await second.get(f"{events_url}/announcements")).json() == []
                pending = (await host.get(f"{events_url}/scheduled")).json()
                assert len(pending) == 1 and pending[0]["target_phase_type"] == "night"
                assert pending[0]["target_round_number"] == 1
                first_advance = {"client_request_id": str(uuid4()),
                                 "expected_phase_type": "day", "expected_round_number": 0}
                assert (await post(host, f"{base}/game/advance", first_advance)).status_code == 200
                assert (await post(host, f"{base}/game/advance", first_advance)).status_code == 200
                custom_visible = (await second.get(f"{events_url}/announcements")).json()
                assert len(custom_visible) == 1 and custom_visible[0]["text"] == "Đêm xuống rồi"
                assert (custom_visible[0]["phase_type"], custom_visible[0]["round_number"]) == ("night", 1)
                assert (await host.get(f"{events_url}/scheduled")).json() == []

                assert (await post(host, f"{status_url}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill"})).status_code == 200
                death = {"client_request_id": str(uuid4()), "publish_timing": "next_phase",
                         "public_cause": "Bị sói tấn công", "reveal_role": True}
                scheduled_death = await post(host, f"{status_url}/announce", death)
                assert scheduled_death.status_code == 200
                assert (await post(host, f"{status_url}/announce", death)).json() == {
                    "event_id": scheduled_death.json()["event_id"], "repeated": True}
                assert (await post(host, f"{status_url}/announce", {
                    **death, "public_cause": "Khác"})).status_code == 409
                board = (await host.get(f"{base}/game/players")).json()
                assert next(item for item in board if item["user_id"] == ids[1])["publication_disposition"] == "scheduled"
                assert (await second.get(f"{base}/game/death-notices")).json() == []
                public_room = (await second.get(base)).json()
                assert next(item for item in public_room["members"] if item["user_id"] == ids[1])["public_is_alive"]
                pending = (await host.get(f"{events_url}/scheduled")).json()
                assert len(pending) == 1 and pending[0]["target_phase_type"] == "day"
                assert pending[0]["target_round_number"] == 1
                async with session_factory()() as db:
                    decision = await db.get(GameEvent, UUID(scheduled_death.json()["event_id"]))
                    assert decision.visibility == "host"
                    assert await db.get(ScheduledAnnouncement, decision.id)
                night_advance = {"client_request_id": str(uuid4()),
                                 "expected_phase_type": "night", "expected_round_number": 1}
                assert (await post(host, f"{base}/game/advance", night_advance)).status_code == 200
                notices = (await second.get(f"{base}/game/death-notices")).json()
                assert len(notices) == 1 and notices[0]["public_cause"] == "Bị sói tấn công"
                assert (notices[0]["phase_type"], notices[0]["round_number"]) == ("day", 1)
                assert not next(item for item in (await second.get(base)).json()["members"]
                                if item["user_id"] == ids[1])["public_is_alive"]
                assert (await host.get(f"{events_url}/scheduled")).json() == []
                assert (await post(host, f"{base}/game/advance", night_advance)).status_code == 200
                assert len((await second.get(f"{base}/game/death-notices")).json()) == 1
                async with session_factory()() as db:
                    released = await db.scalar(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id),
                        GameEvent.kind == "event.scheduled_announcement_released",
                        GameEvent.payload["scheduled_event_id"].astext == scheduled_death.json()["event_id"]))
                    assert released.payload["public_event_id"] == notices[0]["id"]
                    assert (await db.get(GameEvent, UUID(notices[0]["id"]))).visibility == "room"
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for value in ids:
                            user = await db.get(User, UUID(value))
                            if user:
                                await db.delete(user)
                        await db.commit()

    asyncio.run(run())


def test_cancel_and_stale_status_schedule() -> None:
    async def run() -> None:
        ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, first, second = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(3)]
            try:
                for client, name in zip((host, first, second), ("Quản trò hủy", "A", "B")):
                    ids.append((await post(client, "/api/auth/guest", {"display_name": name})).json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Hủy lịch", "seat_limit": 2})).json()
                room_id, code = room["id"], room["code"]
                base = f"/api/rooms/{room_id}"
                for client in (first, second):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{base}/roles", {"name": "Dân", "description": "Mô tả",
                    "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "passive",
                    "quantity": 2})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={"name": "Hủy lịch", "seat_limit": 2,
                    "registration_open": False}, headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200
                events_url = f"{base}/game/events"
                notice = (await post(host, f"{events_url}/announcements", {
                    "client_request_id": str(uuid4()), "text": "Không đăng",
                    "publish_timing": "next_phase"})).json()
                cancel_url = f"{events_url}/scheduled/{notice['event_id']}/cancel"
                cancel_body = {"client_request_id": str(uuid4())}
                assert (await post(second, cancel_url, cancel_body)).status_code == 403
                assert (await post(host, cancel_url, cancel_body)).status_code == 200
                assert (await post(host, cancel_url, cancel_body)).json()["repeated"]
                assert (await post(host, cancel_url, {"client_request_id": str(uuid4())})).status_code == 404
                status_url = f"{base}/game/players/{ids[1]}"
                assert (await post(host, f"{status_url}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill"})).status_code == 200
                assert (await post(host, f"{status_url}/announce", {
                    "client_request_id": str(uuid4()), "publish_timing": "next_phase"})).status_code == 200
                status_schedule = (await host.get(f"{events_url}/scheduled")).json()[0]
                assert status_schedule["kind"] == "status"
                assert (await post(host, f"{events_url}/scheduled/{status_schedule['id']}/cancel", {
                    "client_request_id": str(uuid4())})).status_code == 200
                assert next(item for item in (await host.get(f"{base}/game/players")).json()
                            if item["user_id"] == ids[1])["publication_disposition"] == "pending"
                assert (await post(host, f"{status_url}/announce", {
                    "client_request_id": str(uuid4()), "publish_timing": "next_phase"})).status_code == 200
                assert (await post(host, f"{status_url}/actions", {
                    "client_request_id": str(uuid4()), "action": "revive"})).status_code == 200
                assert (await host.get(f"{events_url}/scheduled")).json() == []
                assert next(item for item in (await host.get(f"{base}/game/players")).json()
                            if item["user_id"] == ids[1])["publication_disposition"] == "published"
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                assert (await second.get(f"{base}/game/death-notices")).json() == []
                assert (await second.get(f"{events_url}/announcements")).json() == []
                assert (await post(host, f"{events_url}/announcements", {
                    "client_request_id": str(uuid4()), "text": "Không tới sáng",
                    "publish_timing": "next_phase"})).status_code == 200
                assert (await post(host, f"{base}/game/finish", {
                    "result_label": "Kết thúc", "result_reason": "Kiểm tra lịch hẹn"})).status_code == 200
                assert (await host.get(f"{events_url}/scheduled")).json() == []
                assert (await second.get(f"{events_url}/announcements")).json() == []
                async with session_factory()() as db:
                    assert not (await db.scalars(select(ScheduledAnnouncement).where(
                        ScheduledAnnouncement.room_id == UUID(room_id)))).all()
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for value in ids:
                            user = await db.get(User, UUID(value))
                            if user:
                                await db.delete(user)
                        await db.commit()

    asyncio.run(run())
