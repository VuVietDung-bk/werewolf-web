"""Real PostgreSQL behavior for custom role catalog and 25-seat distribution."""

import asyncio
from contextlib import AsyncExitStack
from collections import Counter
from uuid import UUID

import httpx
from sqlalchemy import func, select

from app.db.models import GameEvent, RoleAssignment, RoleSnapshot, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, data: dict | None = None) -> httpx.Response:
    return await client.post(path, json=data, headers={"Origin": ORIGIN})


def test_catalog_permissions_and_random_distribution_25_players() -> None:
    async def run() -> None:
        ids: list[str] = []
        room_id = ""
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            clients = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(27)]
            host, outsider, *players = clients
            for index, client in enumerate(clients):
                login = await post(client, "/api/auth/guest", {"display_name": f"Người {index}"})
                assert login.status_code == 200
                ids.append(login.json()["id"])
            created = await post(host, "/api/rooms", {"name": "Kiểm thử role", "seat_limit": 25})
            assert created.status_code == 201
            room_id, code = created.json()["id"], created.json()["code"]
            url = f"/api/rooms/{room_id}"
            assert (await outsider.get(f"{url}/role-catalog")).status_code == 403
            assert (await players[0].get(f"{url}/role-catalog")).status_code == 403
            join = {"code": code, "accept_postgame_transcript": True}
            joined = await asyncio.gather(*(post(client, "/api/rooms/join", join) for client in players))
            assert all(response.status_code == 200 for response in joined)
            assert len((await host.get(url)).json()["members"]) == 25

            passive = {"name": "Dân làng", "description": "Không có kỹ năng chủ động.",
                       "faction_label": "Làng", "seer_result": "Thiện",
                       "role_kind": "passive", "quantity": 20}
            night = {"name": "Sói", "description": "Đêm đề nghị giết; quản trò xác nhận.",
                     "faction_label": "Sói", "seer_result": "Ác", "team_chat_label": "Sói", "role_kind": "active",
                     "request_phases": ["night"], "quantity": 3,
                     "host_contact": {"can_initiate": True, "initiate_phases": ["night"]}}
            day = {"name": "Xạ thủ", "description": "Ngày đề nghị bắn; quản trò xác nhận.",
                   "faction_label": "Làng", "seer_result": "Không rõ", "role_kind": "active", "request_phases": ["day"],
                   "quantity": 2}
            assert (await post(host, f"{url}/roles", {key: value for key, value in passive.items()
                                                        if key != "seer_result"})).status_code == 422
            assert (await post(host, f"{url}/roles", {**passive, "seer_result": "   "})).status_code == 422
            assert (await post(host, f"{url}/roles", {**passive, "request_phases": ["night"]})).status_code == 422
            assert (await post(host, f"{url}/roles", {**passive, "host_contact": {
                "can_initiate": True, "initiate_phases": ["day"]}})).status_code == 422
            assert (await post(players[0], f"{url}/roles", passive)).status_code == 403
            assert (await post(host, f"{url}/roles", passive)).status_code == 201
            assert (await post(host, f"{url}/roles", night)).status_code == 201
            added_day = await post(host, f"{url}/roles", day)
            assert added_day.status_code == 201
            assert (await post(host, f"{url}/roles", {**day, "name": "sói"})).status_code == 409
            assert (await outsider.get(f"{url}/roles/assignments")).status_code == 403
            public = (await players[0].get(f"{url}/role-catalog")).json()
            assert len(public["roles"]) == 3
            assert all("user_id" not in role for role in public["roles"])
            assert any(role["description"] == day["description"] for role in public["roles"])
            assert {role["name"]: role["seer_result"] for role in public["roles"]} == {
                "Dân làng": "Thiện", "Sói": "Ác", "Xạ thủ": "Không rõ"}

            # A mismatch fails without creating a snapshot or leaking partial assignments.
            modified = {**day, "quantity": 1}
            assert (await host.patch(f"{url}/roles/{added_day.json()['id']}/quantity",
                                     json={"quantity": modified["quantity"]},
                                     headers={"Origin": ORIGIN})).status_code == 200
            settings = {"name": "Kiểm thử role", "seat_limit": 25, "registration_open": False}
            assert (await host.patch(f"{url}/settings", json=settings,
                                     headers={"Origin": ORIGIN})).status_code == 200
            assert (await post(host, f"{url}/roles/distribute-random")).status_code == 409
            async with session_factory()() as db:
                assert await db.get(RoleSnapshot, UUID(room_id)) is None
                assert await db.scalar(select(func.count()).select_from(RoleAssignment)
                                       .where(RoleAssignment.room_id == UUID(room_id))) == 0
            assert (await host.patch(f"{url}/roles/{added_day.json()['id']}/quantity",
                                     json={"quantity": day["quantity"]},
                                     headers={"Origin": ORIGIN})).status_code == 200

            results = await asyncio.gather(*(post(host, f"{url}/roles/distribute-random") for _ in range(2)))
            assert sorted(result.status_code for result in results) == [200, 409]
            assigned = next(result.json() for result in results if result.status_code == 200)
            assert len(assigned) == len({item["user_id"] for item in assigned}) == 25
            assert Counter(item["role"]["name"] for item in assigned) == Counter({
                "Dân làng": 20, "Sói": 3, "Xạ thủ": 2})
            assert {item["role"]["name"]: item["role"]["seer_result"] for item in assigned} == {
                "Dân làng": "Thiện", "Sói": "Ác", "Xạ thủ": "Không rõ"}
            assert (await host.get(url)).json()["status"] == "assigned"
            assert sorted((await host.get(f"{url}/roles/assignments")).json(),
                          key=lambda item: item["user_id"]) == sorted(
                              assigned, key=lambda item: item["user_id"])
            assert (await players[0].get(f"{url}/roles/assignments")).status_code == 403
            for index, client in enumerate(players):
                own = (await client.get(f"{url}/roles/my-assignment")).json()
                assert own["user_id"] == ids[index + 2]
                assert own == next(item for item in assigned if item["user_id"] == own["user_id"])
            frozen = (await players[0].get(f"{url}/role-catalog")).json()
            assert frozen["locked"] and frozen["distribution_mode"] == "random"
            assert all("seer_result" in role for role in frozen["roles"])
            assert (await host.put(f"{url}/roles/{added_day.json()['id']}", json={**day,
                "description": "Mô tả đổi"}, headers={"Origin": ORIGIN})).status_code == 409
            assert (await host.patch(f"{url}/roles/{added_day.json()['id']}/quantity",
                                     json={"quantity": 1}, headers={"Origin": ORIGIN})).status_code == 409
            assert (await players[0].get(f"{url}/role-catalog")).json() == frozen
            assert (await post(players[0], f"{url}/leave")).status_code == 409
            assert (await post(players[0], f"{url}/game/start")).status_code == 403
            started_game = await post(host, f"{url}/game/start")
            assert started_game.status_code == 200
            assert started_game.json()["status"] == "playing"
            assert started_game.json()["phase_type"] == "day"
            assert started_game.json()["round_number"] == 0
            assert (await host.patch(f"{url}/settings", json=settings,
                                     headers={"Origin": ORIGIN})).status_code == 409
            async with session_factory()() as db:
                events = (await db.scalars(select(GameEvent).where(GameEvent.room_id == UUID(room_id),
                               GameEvent.kind == "roles.distributed"))).all()
                assert len(events) == 1 and events[0].visibility == "host"
                assert len(events[0].payload["assignments"]) == 25

        async with session_factory()() as db:
            await db.delete(await db.get(Room, UUID(room_id)))
            await db.commit()
            for user_id in ids:
                await db.delete(await db.get(User, UUID(user_id)))
            await db.commit()

    asyncio.run(run())


def test_sample_set_can_be_renamed_edited_and_deleted() -> None:
    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (httpx.AsyncClient(transport=transport, base_url="http://test") as host,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as player):
            host_id = (await post(host, "/api/auth/guest", {"display_name": "Quản trò"})).json()["id"]
            player_id = (await post(player, "/api/auth/guest", {"display_name": "Người chơi"})).json()["id"]
            created = (await post(host, "/api/rooms", {"name": "Bộ mẫu", "seat_limit": 2})).json()
            url = f"/api/rooms/{created['id']}"
            assert (await post(player, "/api/rooms/join", {"code": created["code"],
                         "accept_postgame_transcript": True})).status_code == 200
            assert (await post(player, f"{url}/role-set/sample")).status_code == 403
            sample = await post(host, f"{url}/role-set/sample")
            assert sample.status_code == 200
            assert len(sample.json()["roles"]) == 4
            assert {role["name"]: role["seer_result"] for role in sample.json()["roles"]} == {
                "Dân làng": "Thiện", "Tiên Tri": "Thiện",
                "Sói thường": "Ác", "Xạ thủ": "Không rõ"}
            assert (await post(host, f"{url}/role-set/sample")).status_code == 409
            assert (await player.get(f"{url}/role-catalog")).json() == sample.json()
            renamed = await host.patch(f"{url}/role-set", json={"name": "Làng trăng tối"},
                                       headers={"Origin": ORIGIN})
            assert renamed.status_code == 200 and renamed.json()["name"] == "Làng trăng tối"
            seer = next(role for role in renamed.json()["roles"] if role["name"] == "Tiên Tri")
            assert (await host.patch(f"{url}/roles/{seer['id']}/quantity", json={"quantity": 31},
                                     headers={"Origin": ORIGIN})).status_code == 422
            assert (await player.patch(f"{url}/roles/{seer['id']}/quantity", json={"quantity": 2},
                                       headers={"Origin": ORIGIN})).status_code == 403
            quantity_changed = await host.patch(f"{url}/roles/{seer['id']}/quantity",
                                                json={"quantity": 2}, headers={"Origin": ORIGIN})
            assert quantity_changed.status_code == 200 and quantity_changed.json()["quantity"] == 2
            assert (await player.get(f"{url}/role-catalog")).json()["roles"] != renamed.json()["roles"]
            seer = quantity_changed.json()
            changed = {key: value for key, value in seer.items() if key != "id"}
            changed["description"] = "Đêm gửi lời tiên đoán tự do cho quản trò."
            changed["seer_result"] = "Không rõ"
            updated = await host.put(f"{url}/roles/{seer['id']}", json=changed,
                                     headers={"Origin": ORIGIN})
            assert updated.status_code == 200 and updated.json()["description"] == changed["description"]
            assert updated.json()["seer_result"] == "Không rõ"
            assert (await player.delete(f"{url}/roles/{seer['id']}",
                                        headers={"Origin": ORIGIN})).status_code == 403
            assert (await host.delete(f"{url}/roles/{seer['id']}",
                                      headers={"Origin": ORIGIN})).status_code == 204
            public = (await player.get(f"{url}/role-catalog")).json()
            assert len(public["roles"]) == 3 and public["name"] == "Làng trăng tối"
            assert all(role["id"] != seer["id"] for role in public["roles"])
        async with session_factory()() as db:
            await db.delete(await db.get(Room, UUID(created["id"])))
            await db.commit()
            await db.delete(await db.get(User, UUID(host_id)))
            await db.delete(await db.get(User, UUID(player_id)))
            await db.commit()

    asyncio.run(run())
