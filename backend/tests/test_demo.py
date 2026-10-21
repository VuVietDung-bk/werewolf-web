"""The local demo switcher cannot grant room access without its explicit key."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx

from app import demo
from app.db.models import Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


def test_local_demo_account_switching(monkeypatch, tmp_path) -> None:
    async def run() -> None:
        room_id = None
        ids = []
        transport = httpx.ASGITransport(app=app)
        async with (httpx.AsyncClient(transport=transport, base_url="http://test") as host,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as player):
            try:
                for client, name in ((host, "Quản trò demo"), (player, "Người demo")):
                    response = await client.post("/api/auth/guest", json={"display_name": name},
                                                 headers={"Origin": ORIGIN})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                created = await host.post("/api/rooms", json={"name": "Demo test", "seat_limit": 2},
                                          headers={"Origin": ORIGIN})
                room_id = created.json()["id"]
                joined = await player.post("/api/rooms/join", json={
                    "code": created.json()["code"], "accept_postgame_transcript": True},
                    headers={"Origin": ORIGIN})
                assert joined.status_code == 200

                manifest = tmp_path / "demo.json"
                manifest.write_text(json.dumps({"key": "test-secret", "room_id": room_id,
                    "actors": [{"id": ids[0], "name": "Quản trò demo", "kind": "host"},
                               {"id": ids[1], "name": "Người demo", "kind": "player", "seat": 1}]}))
                monkeypatch.setattr(demo, "MANIFEST", manifest)
                monkeypatch.delenv("ENABLE_DEMO_MODE", raising=False)
                path = "/api/dev/demo"
                assert (await player.get(path, headers={"X-Demo-Key": "test-secret"})).status_code == 404
                monkeypatch.setenv("ENABLE_DEMO_MODE", "1")
                assert (await player.get(path, headers={"X-Demo-Key": "wrong"})).status_code == 403
                listing = await player.get(path, headers={"X-Demo-Key": "test-secret"})
                assert listing.status_code == 200 and len(listing.json()["actors"]) == 2
                assert (await player.post(f"{path}/switch", json={"actor_id": str(uuid4())},
                    headers={"Origin": ORIGIN, "X-Demo-Key": "test-secret"})).status_code == 403
                assert (await player.post(f"{path}/switch", json={"actor_id": ids[0]},
                    headers={"X-Demo-Key": "test-secret"})).status_code == 403
                switched = await player.post(f"{path}/switch", json={"actor_id": ids[0]},
                    headers={"Origin": ORIGIN, "X-Demo-Key": "test-secret"})
                assert switched.status_code == 200
                assert "HttpOnly" in switched.headers["set-cookie"]
                assert (await player.get(f"/api/rooms/{room_id}/access")).json() == {"is_host": True}
                assert (await player.post(f"{path}/switch", json={"actor_id": ids[1]},
                    headers={"Origin": ORIGIN, "X-Demo-Key": "test-secret"})).status_code == 200
                assert (await player.get(f"/api/rooms/{room_id}/access")).json() == {"is_host": False}
                monkeypatch.setenv("APP_ENV", "production")
                assert (await player.get(path, headers={"X-Demo-Key": "test-secret"})).status_code == 404
            finally:
                monkeypatch.setenv("APP_ENV", "local")
                if room_id:
                    async with session_factory()() as db:
                        room = await db.get(Room, UUID(room_id))
                        if room:
                            await db.delete(room)
                            await db.commit()
                async with session_factory()() as db:
                    for user_id in ids:
                        user = await db.get(User, UUID(user_id))
                        if user:
                            await db.delete(user)
                    await db.commit()

    asyncio.run(run())
