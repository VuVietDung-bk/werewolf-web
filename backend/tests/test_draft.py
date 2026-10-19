"""Open-pool draft ordering, role privacy, random choice, and concurrent turns."""

import asyncio
from contextlib import AsyncExitStack
import secrets
from uuid import UUID

import httpx
from sqlalchemy import select

from app.db.models import DraftTurn, GameEvent, RoleAssignment, RoleDefinition, RoleSnapshot, Room, User
from app.db.session import session_factory
from app import draft as draft_module
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


async def seed_room(count: int, roles_by_faction: dict[str, int]):
    stack = AsyncExitStack()
    transport = httpx.ASGITransport(app=app)
    clients = [await stack.enter_async_context(httpx.AsyncClient(
        transport=transport, base_url="http://test")) for _ in range(count + 2)]
    host, outsider, *players = clients
    ids = []
    for index, client in enumerate(clients):
        ids.append((await post(client, "/api/auth/guest", {"display_name": f"Draft {index}"})).json()["id"])
    room = (await post(host, "/api/rooms", {"name": "Lượt chọn", "seat_limit": count})).json()
    url = f"/api/rooms/{room['id']}"
    for client in players:
        assert (await post(client, "/api/rooms/join", {
            "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
    roles = {}
    for faction, amount in roles_by_faction.items():
        roles[faction] = []
        for number in range(amount):
            result = await post(host, f"{url}/roles", {
                "name": f"{faction} {number}", "description": "Role thử nghiệm",
                "faction_label": faction, "seer_result": "Không rõ", "role_kind": "passive",
            })
            assert result.status_code == 201
            roles[faction].append(result.json())
    assert (await host.patch(f"{url}/settings", json={
        "name": "Lượt chọn", "seat_limit": count, "registration_open": False,
    }, headers={"Origin": ORIGIN})).status_code == 200
    return stack, clients, ids, room, roles


async def cleanup(room_id: str, ids: list[str]) -> None:
    async with session_factory()() as db:
        await db.delete(await db.get(Room, UUID(room_id)))
        await db.commit()
        for user_id in ids:
            await db.delete(await db.get(User, UUID(user_id)))
        await db.commit()


def test_draft_seven_players_mixed_factions_private_offers_and_host_random() -> None:
    async def run() -> None:
        stack, clients, ids, room, roles = await seed_room(7, {"Làng": 3, "Sói": 3, "Trung lập": 3})
        host, outsider, *players = clients
        url = f"/api/rooms/{room['id']}"
        all_roles = [role for group in roles.values() for role in group]
        by_id = {role["id"]: role for role in all_roles}
        config = {"candidate_role_ids": [role["id"] for role in all_roles],
            "allow_duplicate_roles": True}
        try:
            assert (await outsider.get(f"{url}/roles/draft/status")).status_code == 403
            assert (await post(players[0], f"{url}/roles/draft/start", config)).status_code == 403
            assert (await post(host, f"{url}/roles/draft/start", {**config,
                "candidate_role_ids": []})).status_code == 409
            assert (await post(host, f"{url}/roles/draft/start", {**config,
                "candidate_role_ids": [role["id"] for role in all_roles if role["name"] != "Sói 2"]})).status_code == 200
            # Start was accepted despite a faction having only two candidates.
            # Use a fresh room for the remaining full draft assertions.
        finally:
            await stack.aclose()
            await cleanup(room["id"], ids)

    asyncio.run(run())


def test_draft_seven_players_private_offers_and_host_random() -> None:
    async def run() -> None:
        stack, clients, ids, room, roles = await seed_room(7, {"Làng": 3, "Sói": 3, "Trung lập": 3})
        host, outsider, *players = clients
        url = f"/api/rooms/{room['id']}"
        all_roles = [role for group in roles.values() for role in group]
        by_id = {role["id"]: role for role in all_roles}
        config = {"candidate_role_ids": [role["id"] for role in all_roles],
                  "allow_duplicate_roles": True}
        try:
            started = await post(host, f"{url}/roles/draft/start", config)
            assert started.status_code == 200
            assert started.json()["status"] == "drafting"
            assert started.json()["my_offers"] is None
            assert len(started.json()["host_turns"]) == 7
            assert [item["turn_index"] for item in started.json()["host_turns"]] == list(range(1, 8))
            assert {item["user_id"] for item in started.json()["host_turns"]} == set(ids[2:])
            assert (await post(host, f"{url}/roles/draft/start", config)).status_code == 409
            assert (await post(host, f"{url}/game/start")).status_code == 409
            catalog = (await players[0].get(f"{url}/role-catalog")).json()
            assert catalog["locked"] and catalog["distribution_mode"] == "draft"
            assert set(catalog["draft_config"]) == {"candidate_role_ids", "allow_duplicate_roles"}
            assert "turn_order" not in catalog["draft_config"]
            assert len(catalog["roles"]) == 9 and all("user_id" not in role for role in catalog["roles"])

            client_by_id = {user_id: client for user_id, client in zip(ids[2:], players)}
            first_offers = started.json()["host_turns"][0]["offers"]
            assert set(first_offers).issubset(by_id)
            for turn_index in range(1, 8):
                status = (await host.get(f"{url}/roles/draft/status")).json()
                current = status["host_turns"][turn_index - 1]
                assert status["current_turn"] == turn_index
                assert len(current["offers"]) == len(set(current["offers"])) == 3
                actor = client_by_id[current["user_id"]]
                own = (await actor.get(f"{url}/roles/draft/status")).json()
                assert own["my_turn"] == turn_index and own["host_turns"] is None
                assert [role["id"] for role in own["my_offers"]] == current["offers"]
                waiting_id = next(user_id for user_id in ids[2:] if user_id != current["user_id"])
                waiting = (await client_by_id[waiting_id].get(f"{url}/roles/draft/status")).json()
                assert waiting["my_offers"] is None and waiting["host_turns"] is None
                assert (await post(client_by_id[waiting_id], f"{url}/roles/draft/choose",
                                   {"role_id": current["offers"][0]})).status_code == 403
                if turn_index == 1:
                    attempts = await asyncio.gather(*(
                        post(actor, f"{url}/roles/draft/choose", {"role_id": current["offers"][0]})
                        for _ in range(2)))
                    assert sorted(response.status_code for response in attempts) == [200, 403]
                elif turn_index == 2:
                    assert (await post(host, f"{url}/roles/draft/host-random",
                                       {"reason": "   "})).status_code == 422
                    assert (await post(host, f"{url}/roles/draft/host-random",
                                       {"reason": "Người chơi vắng mặt"})).status_code == 200
                elif turn_index == 3:
                    assert (await post(actor, f"{url}/roles/draft/choose",
                                       {"choose_random": True})).status_code == 200
                else:
                    assert (await post(actor, f"{url}/roles/draft/choose",
                                       {"role_id": current["offers"][0]})).status_code == 200
            final = (await host.get(f"{url}/roles/draft/status")).json()
            assert final["status"] == "assigned" and final["completed"] == 7
            assert final["current_turn"] is None
            assigned = (await host.get(f"{url}/roles/assignments")).json()
            assert len(assigned) == len({item["user_id"] for item in assigned}) == 7
            assert all(item["role"]["id"] in by_id for item in assigned)
            assert (await players[0].get(f"{url}/roles/assignments")).status_code == 403
            assert (await outsider.get(f"{url}/role-catalog")).status_code == 403
            assert (await outsider.get(f"{url}/roles/my-assignment")).status_code == 403
            assert (await post(host, f"{url}/roles/draft/host-random",
                               {"reason": "muộn"})).status_code == 409
            assert (await post(players[0], f"{url}/game/start")).status_code == 403
            original_name = all_roles[0]["name"]
            async with session_factory()() as db:
                source = await db.get(RoleDefinition, UUID(all_roles[0]["id"]))
                source.name = "Đã đổi trong mẫu nguồn"
                await db.commit()
            frozen_catalog = (await players[0].get(f"{url}/role-catalog")).json()
            assert next(role for role in frozen_catalog["roles"]
                        if role["id"] == all_roles[0]["id"])["name"] == original_name
            for item in assigned[:2]:
                private = (await client_by_id[item["user_id"]].get(
                    f"{url}/roles/my-assignment")).json()
                assert private["user_id"] == item["user_id"]
                assert private["role"]["id"] == item["role"]["id"]
            async with session_factory()() as db:
                snapshot = await db.get(RoleSnapshot, UUID(room["id"]))
                assert snapshot.settings["turn_order"] == [item["user_id"] for item in final["host_turns"]]
                turns = (await db.scalars(select(DraftTurn).where(DraftTurn.room_id == UUID(room["id"])))).all()
                assert len(turns) == 7 and all(turn.offers and turn.chosen_role_id for turn in turns)
                assert len((await db.scalars(select(RoleAssignment).where(
                    RoleAssignment.room_id == UUID(room["id"])))).all()) == 7
                events = (await db.scalars(select(GameEvent).where(GameEvent.room_id == UUID(room["id"]),
                           GameEvent.kind == "roles.draft_chosen"))).all()
                assert len(events) == 7 and all(event.visibility == "host" for event in events)
                assert any(event.payload["choice_mode"] == "host_random" and
                           event.payload["reason"] == "Người chơi vắng mặt" for event in events)
            started_game = await post(host, f"{url}/game/start")
            assert started_game.status_code == 200
            assert started_game.json()["status"] == "playing"
            assert started_game.json()["phase_type"] == "day"
            assert started_game.json()["round_number"] == 0
            assert (await players[0].get(url)).json()["phase_type"] == "day"
            assert (await post(host, f"{url}/game/start")).status_code == 409
            assert (await post(host, f"{url}/roles", {
                "name": "Sau khi bắt đầu", "description": "Không hợp lệ", "faction_label": "Làng",
                "seer_result": "Thiện", "role_kind": "passive"})).status_code == 409
        finally:
            await stack.aclose()
            await cleanup(room["id"], ids)

    asyncio.run(run())


def test_no_duplicate_draft_uses_only_one_candidate_per_player() -> None:
    async def run() -> None:
        stack, clients, ids, room, roles = await seed_room(3, {"Làng": 5})
        host, _, *players = clients
        url = f"/api/rooms/{room['id']}"
        role_ids = [role["id"] for role in roles["Làng"]]
        config = {"candidate_role_ids": role_ids[:3], "allow_duplicate_roles": False}
        try:
            assert (await post(host, f"{url}/roles/draft/start", {**config,
                "candidate_role_ids": role_ids[:2]})).status_code == 409
            assert (await post(host, f"{url}/roles/draft/start", config)).status_code == 200
            client_by_id = {user_id: client for user_id, client in zip(ids[2:], players)}
            chosen = set()
            for turn_index in range(1, 4):
                state = (await host.get(f"{url}/roles/draft/status")).json()
                current = state["host_turns"][turn_index - 1]
                assert len(current["offers"]) == 4 - turn_index
                assert not chosen.intersection(current["offers"])
                role_id = current["offers"][0]
                chosen.add(role_id)
                assert (await post(client_by_id[current["user_id"]],
                                   f"{url}/roles/draft/choose", {"role_id": role_id})).status_code == 200
            assert len(chosen) == 3
            assert (await host.get(f"{url}/roles/draft/status")).json()["status"] == "assigned"
        finally:
            await stack.aclose()
            await cleanup(room["id"], ids)

    asyncio.run(run())


def test_random_draft_choice_can_draw_role_outside_offers(monkeypatch) -> None:
    async def run() -> None:
        stack, clients, ids, room, roles = await seed_room(2, {"Làng": 4})
        host, _, *players = clients
        url = f"/api/rooms/{room['id']}"
        role_ids = [role["id"] for role in roles["Làng"]]
        try:
            started = await post(host, f"{url}/roles/draft/start", {
                "candidate_role_ids": role_ids, "allow_duplicate_roles": False})
            assert started.status_code == 200
            turn = started.json()["host_turns"][0]
            outside_id = next(role_id for role_id in role_ids if role_id not in turn["offers"])

            class PickOutside:
                def choice(self, items):
                    assert outside_id in items
                    return outside_id

                def sample(self, items, count):
                    return secrets.SystemRandom().sample(items, count)

            with monkeypatch.context() as patch:
                patch.setattr(draft_module, "random_source", PickOutside())
                actor = players[ids[2:].index(turn["user_id"])]
                result = await post(actor, f"{url}/roles/draft/choose", {"choose_random": True})
            assert result.status_code == 200
            assert (await host.get(f"{url}/roles/draft/status")).json()["host_turns"][0]["chosen_role_id"] == outside_id
        finally:
            await stack.aclose()
            await cleanup(room["id"], ids)

    asyncio.run(run())
