"""Role icon validation, persistence, snapshotting, and privacy."""

import asyncio
from io import BytesIO
from uuid import UUID, uuid4

import httpx
from PIL import Image
from sqlalchemy import select

from app.db.models import RoleDefinition, RoleIcon, RoleSnapshot, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


def picture(format_name: str = "PNG", size: tuple[int, int] = (64, 64)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "#5a7391").save(output, format=format_name)
    return output.getvalue()


def animated_webp() -> bytes:
    output = BytesIO()
    frames = [Image.new("RGB", (24, 24), color) for color in ("red", "blue")]
    frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    return output.getvalue()


async def post(client: httpx.AsyncClient, path: str, body: dict | None = None) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def test_icon_upload_limits_room_access_and_role_snapshot() -> None:
    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (httpx.AsyncClient(transport=transport, base_url="http://test") as host,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as first,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as second,
                    httpx.AsyncClient(transport=transport, base_url="http://test") as outsider):
            clients = [host, first, second, outsider]
            ids = [(await post(client, "/api/auth/guest", {"display_name": f"Icon {index}"})).json()["id"]
                   for index, client in enumerate(clients)]
            room = (await post(host, "/api/rooms", {"name": "Icon", "seat_limit": 2})).json()
            url = f"/api/rooms/{room['id']}"
            for client in (first, second):
                assert (await post(client, "/api/rooms/join", {
                    "code": room["code"], "accept_postgame_transcript": True})).status_code == 200

            async def upload(client: httpx.AsyncClient, data: bytes, kind: str = "image/png"):
                return await client.post(f"{url}/icons", content=data,
                                         headers={"Origin": ORIGIN, "Content-Type": kind})

            try:
                assert (await upload(first, picture())).status_code == 403
                assert (await upload(host, picture(), "image/svg+xml")).status_code == 415
                assert (await upload(host, picture(), "image/jpeg")).status_code == 415
                assert (await upload(host, b"bad", "image/png")).status_code == 415
                assert (await upload(host, animated_webp(), "image/webp")).status_code == 415
                assert (await upload(host, picture(size=(513, 32)))).status_code == 413
                assert (await upload(host, b"x" * (1024 * 1024 + 1))).status_code == 413
                for kind, format_name in (("image/jpeg", "JPEG"), ("image/webp", "WEBP")):
                    accepted = await upload(host, picture(format_name), kind)
                    assert accepted.status_code == 201
                    assert accepted.json()["width"] == 64
                uploaded = await upload(host, picture())
                assert uploaded.status_code == 201
                icon_id = uploaded.json()["id"]
                icon_url = uploaded.json()["url"]
                assert (await outsider.get(icon_url)).status_code == 403
                response = await first.get(icon_url)
                assert response.status_code == 200
                assert response.headers["content-type"] == "image/png"
                assert response.headers["x-content-type-options"] == "nosniff"
                assert Image.open(BytesIO(response.content)).size == (64, 64)
                assert (await first.get(f"{url}/icons/{uuid4()}")).status_code == 404

                village = {"name": "Tiên Tri", "description": "Soi vào ban đêm", "faction_label": "Làng",
                           "seer_result": "Thiện", "role_kind": "active", "request_phases": ["night"],
                           "quantity": 1, "icon_key": "upload", "icon_id": icon_id}
                wolf = {"name": "Sói", "description": "Đêm thảo luận", "faction_label": "Sói",
                        "seer_result": "Ác", "role_kind": "passive", "quantity": 1,
                        "icon_key": "wolf"}
                assert (await post(host, f"{url}/roles", {**village,
                    "icon_id": str(uuid4())})).status_code == 422
                assert (await post(host, f"{url}/roles", {**wolf, "icon_id": icon_id})).status_code == 422
                created = await post(host, f"{url}/roles", village)
                assert created.status_code == 201
                assert created.json()["icon_id"] == icon_id
                assert (await post(host, f"{url}/roles", wolf)).status_code == 201
                catalog = (await second.get(f"{url}/role-catalog")).json()
                assert {item["icon_key"] for item in catalog["roles"]} == {"upload", "wolf"}
                assert all("user_id" not in item for item in catalog["roles"])
                assert (await host.patch(f"{url}/settings", json={"name": "Icon", "seat_limit": 2,
                    "registration_open": False}, headers={"Origin": ORIGIN})).status_code == 200
                assigned = await post(host, f"{url}/roles/distribute-random")
                assert assigned.status_code == 200
                assert (await upload(host, picture())).status_code == 409
                assert (await outsider.get(f"{url}/role-catalog")).status_code == 403
                for client in (first, second):
                    own = (await client.get(f"{url}/roles/my-assignment")).json()
                    assert own["role"]["icon_key"] in {"upload", "wolf"}
                    assert (await client.get(f"{url}/roles/assignments")).status_code == 403
                    assert (await client.get(icon_url)).status_code == 200
                async with session_factory()() as db:
                    source = await db.get(RoleDefinition, UUID(created.json()["id"]))
                    source.icon_key = "generic"
                    source.icon_id = None
                    await db.commit()
                frozen = (await first.get(f"{url}/role-catalog")).json()
                seer = next(item for item in frozen["roles"] if item["name"] == "Tiên Tri")
                assert seer["icon_key"] == "upload" and seer["icon_id"] == icon_id
                async with session_factory()() as db:
                    snapshot = await db.get(RoleSnapshot, UUID(room["id"]))
                    assert any(item["icon_id"] == icon_id for item in snapshot.definitions)
                    assert await db.scalar(select(RoleIcon).where(RoleIcon.id == UUID(icon_id))) is not None
                # Fetch again through a new request/session: bytes are persisted in PostgreSQL.
                assert (await second.get(icon_url)).content == response.content
            finally:
                async with session_factory()() as db:
                    await db.delete(await db.get(Room, UUID(room["id"])))
                    await db.commit()
                    for user_id in ids:
                        await db.delete(await db.get(User, UUID(user_id)))
                    await db.commit()

    asyncio.run(run())
