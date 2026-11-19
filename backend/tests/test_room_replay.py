"""A person occupies one active room; a finished game can reopen its lobby."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID

import httpx
from sqlalchemy import delete, select

from app.db.models import RoleDefinition, RoleIcon, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_one_active_room_and_next_game_keeps_history() -> None:
    async def run() -> None:
        ids: list[str] = []
        transport = httpx.ASGITransport(app=app)
        async with AsyncExitStack() as stack:
            host, other_host, first, second = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(4)]
            try:
                for client, name in zip((host, other_host, first, second),
                                        ("Host replay", "Other host", "First replay", "Second replay")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    ids.append(response.json()["id"])
                initial = await post(host, "/api/rooms", {"name": "Làng cũ", "seat_limit": 2})
                assert initial.status_code == 201
                old = initial.json()
                other = (await post(other_host, "/api/rooms", {
                    "name": "Làng khác", "seat_limit": 2})).json()
                assert (await post(host, "/api/rooms", {
                    "name": "Trùng quản trò", "seat_limit": 2})).status_code == 409
                assert (await post(host, "/api/rooms/join", {
                    "code": other["code"], "accept_postgame_transcript": True})).status_code == 409
                assert (await post(first, "/api/rooms/join", {
                    "code": old["code"], "accept_postgame_transcript": True})).status_code == 200
                assert (await post(first, "/api/rooms", {
                    "name": "Người chơi tạo phòng", "seat_limit": 2})).status_code == 409
                assert (await post(first, "/api/rooms/join", {
                    "code": other["code"], "accept_postgame_transcript": True})).status_code == 409

                # Concurrent entry for the same account must claim only one lobby.
                attempts = await asyncio.gather(*(
                    post(second, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})
                    for code in (old["code"], other["code"])))
                assert sorted(response.status_code for response in attempts) == [200, 409]
                if attempts[1].status_code == 200:
                    assert (await post(second, f"/api/rooms/{other['id']}/leave")).status_code == 204
                    assert (await post(second, "/api/rooms/join", {
                        "code": old["code"], "accept_postgame_transcript": True})).status_code == 200
                base = f"/api/rooms/{old['id']}"
                role = {"name": "Dân làng", "description": "Thảo luận trong ngày.",
                        "faction_label": "Làng", "seer_result": "Thiện",
                        "role_kind": "passive", "quantity": 2}
                created_role = await post(host, f"{base}/roles", role)
                assert created_role.status_code == 201
                async with session_factory()() as db:
                    icon = RoleIcon(room_id=UUID(old["id"]), image_bytes=b"sample icon",
                                    width=8, height=8)
                    db.add(icon)
                    await db.flush()
                    old_icon_id = icon.id
                    definition = await db.get(RoleDefinition, UUID(created_role.json()["id"]))
                    definition.icon_key = "upload"
                    definition.icon_id = old_icon_id
                    db.add(RoleIcon(room_id=UUID(old["id"]), image_bytes=b"unused icon",
                                    width=8, height=8))
                    await db.commit()
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Làng cũ", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200
                assert (await post(first, f"{base}/leave")).status_code == 409
                ended = await post(host, f"{base}/game/finish", {
                    "result_label": "Làng thắng", "result_reason": "Quản trò chốt kết quả"})
                assert ended.status_code == 200 and ended.json()["status"] == "finished"
                assert (await post(first, f"{base}/leave")).status_code == 204
                assert (await first.get(f"{base}/transcript")).status_code == 200
                assert (await post(second, "/api/rooms/join", {
                    "code": other["code"], "accept_postgame_transcript": True})).status_code == 200

                # An active third lobby blocks reopening, even for a finished game's host.
                temporary = (await post(host, "/api/rooms", {
                    "name": "Phòng tạm", "seat_limit": 2})).json()
                assert (await post(host, f"{base}/game/next")).status_code == 409
                assert (await post(host, f"/api/rooms/{temporary['id']}/cancel")).status_code == 204
                assert temporary["id"] not in [item["id"] for item in (await host.get("/api/rooms/mine")).json()]

                reopened = await post(host, f"{base}/game/next")
                assert reopened.status_code == 200
                next_room = reopened.json()
                assert next_room["id"] != old["id"] and next_room["code"] == old["code"]
                assert next_room["status"] == "open" and next_room["round_number"] == 0
                assert next_room["members"] == []  # Neither a leaver nor a busy player is carried.
                assert (await post(host, f"{base}/game/next")).json()["id"] == next_room["id"]
                assert (await first.get(f"{base}/transcript")).status_code == 200
                assert (await host.get(base)).json()["next_room_id"] == next_room["id"]
                catalog = (await host.get(f"/api/rooms/{next_room['id']}/role-catalog")).json()
                assert not catalog["locked"] and catalog["roles"][0]["name"] == "Dân làng"
                assert catalog["roles"][0]["id"] != created_role.json()["id"]
                assert catalog["roles"][0]["quantity"] == 2
                async with session_factory()() as db:
                    copied_icon = await db.get(RoleIcon, UUID(catalog["roles"][0]["icon_id"]))
                    assert copied_icon.room_id == UUID(next_room["id"])
                    assert copied_icon.id != old_icon_id and copied_icon.image_bytes == b"sample icon"
                    copied_icons = (await db.scalars(select(RoleIcon).where(
                        RoleIcon.room_id == UUID(next_room["id"])))).all()
                    assert len(copied_icons) == 1
                assert (await post(first, "/api/rooms/join", {
                    "code": old["code"], "accept_postgame_transcript": True})).status_code == 200
                assert (await post(second, f"/api/rooms/{other['id']}/leave")).status_code == 204
                assert (await post(second, "/api/rooms/join", {
                    "code": old["code"], "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, "/api/rooms", {
                    "name": "Không thể tạo song song", "seat_limit": 2})).status_code == 409
                next_base = f"/api/rooms/{next_room['id']}"
                assert (await host.patch(f"{next_base}/settings", json={
                    "name": "Làng cũ", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{next_base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{next_base}/game/start")).json()["status"] == "playing"
                assert (await post(host, f"{next_base}/game/finish", {
                    "result_label": "Ván hai", "result_reason": "Kết thúc ván mới"})).json()["status"] == "finished"
                assert (await first.get(f"{next_base}/transcript")).status_code == 200
                assert (await first.get(f"{base}/transcript")).status_code == 200

                # A lobby already in the assignment step can still be cancelled.
                retry = (await post(host, "/api/rooms", {
                    "name": "Sảnh cần hủy", "seat_limit": 2})).json()
                retry_base = f"/api/rooms/{retry['id']}"
                for player in (first, second):
                    assert (await post(player, "/api/rooms/join", {
                        "code": retry["code"], "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{retry_base}/roles", role)).status_code == 201
                assert (await host.patch(f"{retry_base}/settings", json={
                    "name": "Sảnh cần hủy", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{retry_base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{retry_base}/cancel")).status_code == 204
                assert (await post(host, "/api/rooms", {
                    "name": "Sảnh mới sau hủy", "seat_limit": 2})).status_code == 201
                assert (await post(second, "/api/rooms", {
                    "name": "Hết phòng cũ", "seat_limit": 2})).status_code == 201
            finally:
                async with session_factory()() as db:
                    owned = (await db.scalars(select(Room.id).where(
                        Room.owner_user_id.in_([UUID(item) for item in ids])))).all()
                    await db.execute(delete(Room).where(Room.id.in_(owned)))
                    await db.commit()
                    await db.execute(delete(User).where(User.id.in_([UUID(item) for item in ids])))
                    await db.commit()

    asyncio.run(run())
