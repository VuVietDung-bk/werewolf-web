"""WebSocket fanout is scoped by session, room, and per-event recipients."""

import asyncio
import json
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app import live
from app.auth import COOKIE_NAME
from app.db.events import append_event
from app.db.models import GameEvent, Outbox, PlayerState, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


class ASGISocket:
    def __init__(self, path: str, cookie: str | None, origin: str | None = ORIGIN,
                 after_sequence: int | str | None = None):
        self.input: asyncio.Queue[dict] = asyncio.Queue()
        self.output: asyncio.Queue[dict] = asyncio.Queue()
        headers = []
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        if cookie:
            headers.append((b"cookie", f"{COOKIE_NAME}={cookie}".encode()))
        self.scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
                      "path": path, "raw_path": path.encode(),
                      "query_string": (f"after_sequence={after_sequence}".encode()
                                       if after_sequence is not None else b""),
                      "headers": headers, "client": ("test", 1234),
                      "server": ("test", 80), "subprotocols": []}
        self.task: asyncio.Task | None = None

    async def connect(self) -> dict:
        self.task = asyncio.create_task(app(self.scope, self.input.get, self.output.put))
        await self.input.put({"type": "websocket.connect"})
        return await asyncio.wait_for(self.output.get(), timeout=2)

    async def event(self, event_id: str, timeout: float = 1) -> dict:
        async def matching():
            while True:
                frame = await self.output.get()
                if frame["type"] == "websocket.close":
                    return frame
                if frame["type"] == "websocket.send" and frame.get("text"):
                    import json
                    notice = json.loads(frame["text"])
                    if notice.get("event_id") == event_id:
                        return notice
        return await asyncio.wait_for(matching(), timeout=timeout)

    async def ready(self) -> dict:
        import json
        frame = await asyncio.wait_for(self.output.get(), timeout=2)
        assert frame["type"] == "websocket.send"
        return json.loads(frame["text"])

    async def close(self) -> None:
        if self.task is None or self.task.done():
            return
        await self.input.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self.task, timeout=2)


def chat_body(channel: str, text: str, **extra):
    return {"client_request_id": str(uuid4()), "channel": channel, "text": text, **extra}


def test_room_websocket_delivers_only_authorized_event_ids(monkeypatch) -> None:
    monkeypatch.setattr(live, "POLL_INTERVAL_SECONDS", 0.02)

    async def run() -> None:
        room_id = None
        user_ids = []
        sockets: list[ASGISocket] = []
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            clients = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(5)]
            host, first, second, third, outsider = clients
            try:
                for client, name in zip(clients, ("Live host", "Live A", "Live B", "Live C", "Live outsider")):
                    response = await post(client, "/api/auth/guest", {"display_name": name})
                    assert response.status_code == 200
                    user_ids.append(response.json()["id"])
                created = await post(host, "/api/rooms", {"name": "Live quyền chat", "seat_limit": 3})
                assert created.status_code == 201
                room_id, code = created.json()["id"], created.json()["code"]
                base = f"/api/rooms/{room_id}"
                for client in (first, second, third):
                    assert (await post(client, "/api/rooms/join", {
                        "code": code, "accept_postgame_transcript": True})).status_code == 200
                for name, quantity, team in (("Live Sói", 2, "Bầy sói"), ("Live Dân", 1, None)):
                    assert (await post(host, f"{base}/roles", {
                        "name": name, "description": name, "faction_label": "Sói" if team else "Làng",
                        "seer_result": "Ác" if team else "Thiện", "team_chat_label": team,
                        "role_kind": "passive", "quantity": quantity})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Live quyền chat", "seat_limit": 3, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assignments = (await host.get(f"{base}/roles/assignments")).json()
                wolves = [item["user_id"] for item in assignments if item["role"]["team_chat_label"]]
                villager = next(item["user_id"] for item in assignments if not item["role"]["team_chat_label"])
                by_id = dict(zip(user_ids[1:4], clients[1:4]))
                path = f"{base}/live"
                for client in clients[:4]:
                    socket = ASGISocket(path, client.cookies.get(COOKIE_NAME))
                    assert (await socket.connect())["type"] == "websocket.accept"
                    sockets.append(socket)
                socket_by_id = dict(zip(user_ids[:4], sockets))
                assert (await ASGISocket(path, outsider.cookies.get(COOKIE_NAME)).connect())["code"] == 4403
                assert (await ASGISocket(path, None).connect())["code"] == 4401
                assert (await ASGISocket(path, host.cookies.get(COOKIE_NAME),
                                         "https://evil.example").connect())["code"] == 4403
                assert (await ASGISocket(f"/api/rooms/{uuid4()}/live",
                                         host.cookies.get(COOKIE_NAME)).connect())["code"] == 4404

                chat = f"{base}/chat/messages"
                common = await post(first, chat, chat_body("common", "Tin chung"))
                assert common.status_code == 200
                common_id = common.json()["event_id"]
                for socket in sockets:
                    notice = await socket.event(common_id)
                    assert set(notice) == {"type", "event_id", "sequence", "kind"}
                    assert notice["kind"] == "chat.sent"

                mute = await host.patch(f"{base}/chat/players/{user_ids[1]}/mute",
                    json={"client_request_id": str(uuid4()), "muted": True, "reason": "Thử live"},
                    headers={"Origin": ORIGIN})
                assert mute.status_code == 200
                mute_id = mute.json()["event_id"]
                for user_id in (user_ids[0], user_ids[1]):
                    assert (await socket_by_id[user_id].event(mute_id))["kind"] == "chat.mute_changed"
                for user_id in (user_ids[2], user_ids[3]):
                    try:
                        await socket_by_id[user_id].event(mute_id, timeout=0.2)
                        assert False, "Người khác nhận thông báo chặn chat"
                    except asyncio.TimeoutError:
                        pass
                assert (await host.patch(f"{base}/chat/players/{user_ids[1]}/mute",
                    json={"client_request_id": str(uuid4()), "muted": False, "reason": "Tiếp tục"},
                    headers={"Origin": ORIGIN})).status_code == 200

                assert (await post(host, f"{base}/game/start")).status_code == 200
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                team = await post(by_id[wolves[0]], chat, chat_body("team", "Bí mật của bầy"))
                assert team.status_code == 200
                team_id = team.json()["event_id"]
                for user_id in (user_ids[0], *wolves):
                    assert (await socket_by_id[user_id].event(team_id))["event_id"] == team_id
                try:
                    await socket_by_id[villager].event(team_id, timeout=0.2)
                    assert False, "Người ngoài đội nhận thông báo team"
                except asyncio.TimeoutError:
                    pass

                secret = await post(host, chat, chat_body("host", "Tin riêng của C",
                                                         participant_user_id=villager))
                assert secret.status_code == 200
                secret_id = secret.json()["event_id"]
                for user_id in (user_ids[0], villager):
                    assert (await socket_by_id[user_id].event(secret_id))["event_id"] == secret_id
                for user_id in wolves:
                    try:
                        await socket_by_id[user_id].event(secret_id, timeout=0.2)
                        assert False, "Sói nhận thông báo tin riêng của người khác"
                    except asyncio.TimeoutError:
                        pass

                changed = await post(host, f"{base}/game/players/{wolves[1]}/actions", {
                    "client_request_id": str(uuid4()), "action": "change_team",
                    "text": "Đội mới", "reason": "Thử quyền live"})
                assert changed.status_code == 200
                changed_id = changed.json()["event_id"]
                assert (await socket_by_id[wolves[1]].event(changed_id))["event_id"] == changed_id
                team_after = await post(by_id[wolves[0]], chat, chat_body("team", "Sau khi đổi đội"))
                assert team_after.status_code == 200
                after_id = team_after.json()["event_id"]
                assert (await socket_by_id[wolves[0]].event(after_id))["event_id"] == after_id
                assert (await socket_by_id[user_ids[0]].event(after_id))["event_id"] == after_id
                for user_id in (wolves[1], villager):
                    try:
                        await socket_by_id[user_id].event(after_id, timeout=0.2)
                        assert False, "Người mất quyền nhận tin đội mới"
                    except asyncio.TimeoutError:
                        pass

                note = await post(host, f"{base}/game/players/{villager}/actions", {
                    "client_request_id": str(uuid4()), "action": "note", "text": "Ghi chú kín"})
                assert note.status_code == 200
                note_id = note.json()["event_id"]
                assert (await socket_by_id[user_ids[0]].event(note_id))["event_id"] == note_id
                for user_id in (wolves[0], wolves[1], villager):
                    try:
                        await socket_by_id[user_id].event(note_id, timeout=0.2)
                        assert False, "Người chơi nhận sự kiện ghi chú kín"
                    except asyncio.TimeoutError:
                        pass

                legacy = await post(host, f"{base}/game/players/{villager}/private-messages", {
                    "client_request_id": str(uuid4()), "text": "Tin quản trò từ bàn điều khiển"})
                assert legacy.status_code == 200
                legacy_id = legacy.json()["event_id"]
                for user_id in (user_ids[0], villager):
                    assert (await socket_by_id[user_id].event(legacy_id))["event_id"] == legacy_id
                for user_id in wolves:
                    try:
                        await socket_by_id[user_id].event(legacy_id, timeout=0.2)
                        assert False, "Người khác nhận thông báo tin riêng cũ"
                    except asyncio.TimeoutError:
                        pass

                assert (await post(by_id[villager], "/api/auth/logout")).status_code == 204
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "night", "expected_round_number": 1})).status_code == 200
                assert (await socket_by_id[villager].event("never", timeout=1))["code"] == 4403
            finally:
                for socket in sockets:
                    await socket.close()
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in user_ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())


def test_websocket_replays_missing_events_by_connection_cursor(monkeypatch) -> None:
    monkeypatch.setattr(live, "POLL_INTERVAL_SECONDS", 0.02)

    async def run() -> None:
        room_id = None
        user_ids = []
        sockets: list[ASGISocket] = []
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host = await stack.enter_async_context(httpx.AsyncClient(transport=transport, base_url="http://test"))
            player = await stack.enter_async_context(httpx.AsyncClient(transport=transport, base_url="http://test"))
            try:
                for client, name in ((host, "Replay host"), (player, "Replay player")):
                    user_ids.append((await post(client, "/api/auth/guest", {"display_name": name})).json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Replay", "seat_limit": 2})).json()
                room_id = room["id"]
                assert (await post(player, "/api/rooms/join", {
                    "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                path = f"/api/rooms/{room_id}/live"
                host_socket = ASGISocket(path, host.cookies.get(COOKIE_NAME))
                player_socket = ASGISocket(path, player.cookies.get(COOKIE_NAME))
                sockets.extend([host_socket, player_socket])
                for socket in sockets:
                    assert (await socket.connect())["type"] == "websocket.accept"
                initial = await player_socket.ready()
                assert initial["type"] == "ready"
                await host_socket.ready()
                await player_socket.close()
                async with session_factory()() as db:
                    public_a = await append_event(db, UUID(room_id), UUID(user_ids[0]),
                                                  "room.test_a", "room", {})
                    secret = await append_event(db, UUID(room_id), UUID(user_ids[0]),
                                                "room.secret", "host", {})
                    public_b = await append_event(db, UUID(room_id), UUID(user_ids[0]),
                                                  "room.test_b", "room", {})
                    await db.commit()
                    ids = [str(public_a.id), str(secret.id), str(public_b.id)]
                for event_id in ids:
                    assert (await host_socket.event(event_id))["event_id"] == event_id
                assert (await ASGISocket(path, player.cookies.get(COOKIE_NAME),
                                         after_sequence="bad").connect())["code"] == 4400
                recovered = ASGISocket(path, player.cookies.get(COOKIE_NAME),
                                        after_sequence=initial["cursor"])
                sockets.append(recovered)
                assert (await recovered.connect())["type"] == "websocket.accept"
                assert (await recovered.ready())["cursor"] == initial["cursor"]
                assert (await recovered.event(ids[0]))["event_id"] == ids[0]
                assert (await recovered.event(ids[2]))["event_id"] == ids[2]
                try:
                    await recovered.event(ids[1], timeout=0.2)
                    assert False, "Private event replayed to player"
                except asyncio.TimeoutError:
                    pass
                assert recovered.output.empty()
                assert host_socket.output.empty()
            finally:
                for socket in sockets:
                    await socket.close()
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in user_ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())


def test_reconnect_replays_chat_request_kill_and_vote_publication_in_order(monkeypatch) -> None:
    """Checkpoint C: a real room cursor crosses private and public event types."""
    monkeypatch.setattr(live, "POLL_INTERVAL_SECONDS", 0.02)

    async def received(socket: ASGISocket, event_id: str) -> dict:
        return await socket.event(event_id, timeout=5)

    async def run() -> None:
        room_id = None
        user_ids = []
        sockets: list[ASGISocket] = []
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, first, second = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(3)]
            try:
                for client, name in ((host, "C host"), (first, "C first"), (second, "C second")):
                    user_ids.append((await post(client, "/api/auth/guest", {
                        "display_name": name})).json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Kiểm tra nối lại C", "seat_limit": 2})).json()
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                for client in (first, second):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                for name, kind, phases in (("C chủ động", "active", ["day"]),
                                           ("C bị động", "passive", [])):
                    assert (await post(host, f"{base}/roles", {
                        "name": name, "description": name, "faction_label": "Làng",
                        "seer_result": "Thiện", "role_kind": kind,
                        "request_phases": phases, "quantity": 1})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": room["name"], "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assignments = (await host.get(f"{base}/roles/assignments")).json()
                active_id = next(item["user_id"] for item in assignments
                                 if item["role"]["role_kind"] == "active")
                active = first if active_id == user_ids[1] else second
                witness = second if active is first else first
                assert (await post(host, f"{base}/game/start")).status_code == 200

                path = f"{base}/live"
                for client in (host, active, witness):
                    socket = ASGISocket(path, client.cookies.get(COOKIE_NAME))
                    assert (await socket.connect())["type"] == "websocket.accept"
                    sockets.append(socket)
                host_socket, active_socket, witness_socket = sockets
                await host_socket.ready()
                initial_cursor = (await active_socket.ready())["cursor"]
                assert (await witness_socket.ready())["cursor"] == initial_cursor
                await active_socket.close()
                await witness_socket.close()

                chat = await post(witness, f"{base}/chat/messages", chat_body("common", "C: tin chung"))
                request = await post(active, f"{base}/role-requests", {
                    "client_request_id": str(uuid4()), "text": "C: đoán số lượng"})
                kill = await post(host, f"{base}/game/players/{active_id}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill", "reason": "C: giữ kín"})
                assert [result.status_code for result in (chat, request, kill)] == [200, 200, 200]
                ids = [result.json()["event_id"] for result in (chat, request, kill)]
                host_frames = [await received(host_socket, event_id) for event_id in ids]
                assert [frame["sequence"] for frame in host_frames] == sorted(
                    frame["sequence"] for frame in host_frames)
                assert all(set(frame) == {"type", "event_id", "sequence", "kind"}
                           for frame in host_frames)

                recovered_active = ASGISocket(path, active.cookies.get(COOKIE_NAME),
                                              after_sequence=initial_cursor)
                sockets.append(recovered_active)
                assert (await recovered_active.connect())["type"] == "websocket.accept"
                assert (await recovered_active.ready())["cursor"] == initial_cursor
                active_frames = [await received(recovered_active, event_id) for event_id in ids]
                assert [frame["sequence"] for frame in active_frames] == [
                    frame["sequence"] for frame in host_frames]
                assert recovered_active.output.empty()

                recovered_witness = ASGISocket(path, witness.cookies.get(COOKIE_NAME),
                                               after_sequence=initial_cursor)
                sockets.append(recovered_witness)
                assert (await recovered_witness.connect())["type"] == "websocket.accept"
                await recovered_witness.ready()
                assert (await received(recovered_witness, ids[0]))["event_id"] == ids[0]
                await asyncio.sleep(0.1)
                assert recovered_witness.output.empty()
                public_member = next(item for item in (await witness.get(base)).json()["members"]
                                     if item["user_id"] == active_id)
                assert public_member["public_is_alive"] is True
                assert (await witness.get(f"{base}/role-requests")).json()["requests"] == []
                assert (await host.get(f"{base}/game/players")).json()[
                    0 if active_id == user_ids[1] else 1]["is_alive"] is False

                await recovered_witness.close()
                vote_cursor = (await host.get(base)).json()["version"]
                votes = f"{base}/votes"
                opened = await post(host, votes, {"client_request_id": str(uuid4())})
                assert opened.status_code == 200
                vote_id = opened.json()["vote_round_id"]
                ballots = f"{votes}/{vote_id}/ballots"
                cast = await post(witness, ballots, {"client_request_id": str(uuid4()),
                    "choice": "target", "target_user_id": active_id})
                changed = await post(witness, ballots, {"client_request_id": str(uuid4()),
                    "choice": "skip"})
                withdrawn = await post(witness, ballots, {"client_request_id": str(uuid4()),
                    "choice": "withdraw"})
                assert [response.status_code for response in (cast, changed, withdrawn)] == [200, 200, 200]
                assert (await witness.get(f"{votes}/latest")).json()["my_choice"] == "withdrawn"
                review = (await host.get(f"{votes}/{vote_id}/review")).json()
                by_voter = {item["voter_user_id"]: item["choice"] for item in review["ballots"]}
                assert by_voter == {active_id: None,
                                    (user_ids[2] if active is first else user_ids[1]): "withdrawn"}
                closed = await post(host, f"{votes}/{vote_id}/close", {
                    "client_request_id": str(uuid4())})
                assert closed.status_code == 200
                decided = await post(host, f"{votes}/{vote_id}/decision", {
                    "client_request_id": str(uuid4()), "decision_text": "Không loại ai",
                    "reason": "C: quản trò tự xét"})
                assert decided.status_code == 200
                published = await post(host, f"{votes}/{vote_id}/publication", {
                    "client_request_id": str(uuid4()), "include_result": True,
                    "include_voter_names": False, "include_reason": False})
                assert published.status_code == 200
                original_id = published.json()["public_event_id"]
                revision = await post(host, f"{base}/game/content/{original_id}/revisions", {
                    "client_request_id": str(uuid4()), "action": "edit",
                    "text": "C: đã đính chính kết quả"})
                assert revision.status_code == 200
                public_revision_id = revision.json()["public_event_id"]
                assert (await witness.get(f"{votes}/publications")).json()[0]["text"] == "C: đã đính chính kết quả"
                async with session_factory()() as db:
                    original = await db.get(GameEvent, UUID(original_id))
                    assert original.payload["text"] != "C: đã đính chính kết quả"

                replay = ASGISocket(path, witness.cookies.get(COOKIE_NAME),
                                    after_sequence=vote_cursor)
                sockets.append(replay)
                assert (await replay.connect())["type"] == "websocket.accept"
                assert (await replay.ready())["cursor"] == vote_cursor
                public_ids = [opened.json()["event_id"], closed.json()["event_id"],
                              original_id, public_revision_id]
                frames = []
                for _ in public_ids:
                    try:
                        raw = await asyncio.wait_for(replay.output.get(), timeout=5)
                    except asyncio.TimeoutError as error:
                        raise AssertionError(f"Public replay incomplete: {frames}; expected {public_ids}; "
                                             f"cursor {vote_cursor}") from error
                    assert raw["type"] == "websocket.send"
                    frames.append(json.loads(raw["text"]))
                assert [frame["event_id"] for frame in frames] == public_ids
                assert [frame["sequence"] for frame in frames] == sorted(
                    frame["sequence"] for frame in frames)
                assert all(set(frame) == {"type", "event_id", "sequence", "kind"}
                           for frame in frames)
                assert replay.output.empty()
                host_ids = [opened.json()["event_id"], *(item.json()["event_id"]
                    for item in (cast, changed, withdrawn, closed, decided)), original_id,
                    public_revision_id]
                host_vote_frames = [await received(host_socket, event_id) for event_id in host_ids]
                assert [frame["event_id"] for frame in host_vote_frames] == host_ids
                assert [frame["sequence"] for frame in host_vote_frames] == sorted(
                    frame["sequence"] for frame in host_vote_frames)
            finally:
                for socket in sockets:
                    await socket.close()
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in user_ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())


def test_hidden_or_skipped_death_does_not_leak_through_public_views_or_websocket(monkeypatch) -> None:
    monkeypatch.setattr(live, "POLL_INTERVAL_SECONDS", 0.02)

    async def run() -> None:
        room_id = None
        user_ids = []
        sockets: list[ASGISocket] = []
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, target, witness, outsider = [await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")) for _ in range(4)]
            try:
                for client, name in zip((host, target, witness, outsider),
                                        ("Host ẩn chết", "Mục tiêu", "Nhân chứng", "Người ngoài")):
                    user_ids.append((await post(client, "/api/auth/guest", {
                        "display_name": name})).json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Thử ẩn chết", "seat_limit": 2})).json()
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                for client in (target, witness):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                assert (await post(host, f"{base}/roles", {
                    "name": "Dân", "description": "Thử thông báo", "faction_label": "Làng",
                    "seer_result": "Thiện", "role_kind": "passive", "quantity": 2})).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Thử ẩn chết", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assert (await post(host, f"{base}/game/start")).status_code == 200
                path = f"{base}/live"
                for client in (host, target, witness):
                    socket = ASGISocket(path, client.cookies.get(COOKIE_NAME))
                    sockets.append(socket)
                    assert (await socket.connect())["type"] == "websocket.accept"
                    await socket.ready()
                host_socket, target_socket, witness_socket = sockets
                public_version = (await witness.get(base)).json()["version"]
                action_url = f"{base}/game/players/{user_ids[1]}"
                decision_url = f"{action_url}/publication-disposition"
                assert (await post(host, decision_url, {"client_request_id": str(uuid4()),
                    "decision": "hold", "reason": "Chưa chết"})).status_code == 409
                killed = await post(host, f"{action_url}/actions", {
                    "client_request_id": str(uuid4()), "action": "kill", "reason": "Role che thông báo"})
                assert killed.status_code == 200
                kill_id = killed.json()["event_id"]
                assert (await host_socket.event(kill_id))["kind"] == "player.kill"
                assert (await target_socket.event(kill_id))["kind"] == "player.kill"
                try:
                    await witness_socket.event(kill_id, timeout=0.2)
                    assert False, "Người khác nhận tín hiệu giết kín"
                except asyncio.TimeoutError:
                    pass
                assert (await post(target, decision_url, {"client_request_id": str(uuid4()),
                    "decision": "hold", "reason": "Thử quyền"})).status_code == 403
                assert (await post(outsider, decision_url, {"client_request_id": str(uuid4()),
                    "decision": "hold", "reason": "Thử quyền"})).status_code == 403
                assert (await post(host, decision_url, {"client_request_id": str(uuid4()),
                    "decision": "hold", "reason": "  "})).status_code == 422
                hold_body = {"client_request_id": str(uuid4()), "decision": "hold",
                             "reason": "Đợi tác động role hết hiệu lực"}
                held = await post(host, decision_url, hold_body)
                assert held.status_code == 200
                assert (await post(host, decision_url, hold_body)).json()["repeated"] is True
                assert (await post(host, decision_url, {**hold_body,
                    "reason": "Lý do khác"})).status_code == 409
                assert (await host_socket.event(held.json()["event_id"]))["kind"] == "player.publication_disposition_decided"
                for socket in (target_socket, witness_socket):
                    try:
                        await socket.event(held.json()["event_id"], timeout=0.2)
                        assert False, "Người chơi nhận quyết định giữ kín"
                    except asyncio.TimeoutError:
                        pass
                board = (await host.get(f"{base}/game/players")).json()
                assert next(item for item in board if item["user_id"] == user_ids[1])["publication_disposition"] == "held"
                assert (await witness.get(f"{base}/game/players")).status_code == 403
                assert (await witness.get(base)).json()["version"] == public_version
                assert next(item for item in (await witness.get(base)).json()["members"]
                            if item["user_id"] == user_ids[1])["public_is_alive"]
                assert (await witness.get(f"{base}/game/death-notices")).json() == []

                skipped = await post(host, decision_url, {"client_request_id": str(uuid4()),
                    "decision": "skip", "reason": "Role nuốt thông báo người chết"})
                assert skipped.status_code == 200
                assert (await host_socket.event(skipped.json()["event_id"]))["kind"] == "player.publication_disposition_decided"
                for socket in (target_socket, witness_socket):
                    try:
                        await socket.event(skipped.json()["event_id"], timeout=0.2)
                        assert False, "Người chơi nhận quyết định bỏ qua"
                    except asyncio.TimeoutError:
                        pass
                assert (await witness.get(base)).json()["version"] == public_version
                assert (await witness.get(f"{base}/game/death-notices")).json() == []
                assert (await witness.get(f"{base}/votes/latest")).json() is None
                assert next(item for item in (await host.get(f"{base}/game/players")).json()
                            if item["user_id"] == user_ids[1])["publication_disposition"] == "skipped"
                async with session_factory()() as db:
                    event = await db.get(GameEvent, UUID(skipped.json()["event_id"]))
                    assert event.visibility == "host"
                    assert event.payload["request"]["reason"] == "Role nuốt thông báo người chết"
                    assert event.payload["status_event_id"] == kill_id
                    assert await db.scalar(select(Outbox.id).where(Outbox.event_id == event.id))
                    state = await db.get(PlayerState, (UUID(room_id), UUID(user_ids[1])))
                    assert not state.is_alive and state.public_is_alive

                # Public daytime events may name or hide the source without revealing
                # the concealed death or the moderator's linked decision.
                announcements = f"{base}/game/events/announcements"
                named = await post(host, announcements, {
                    "client_request_id": str(uuid4()), "kind": "attack",
                    "text": "Có một cuộc tấn công giữa làng.",
                    "source_user_id": user_ids[2]})
                anonymous = await post(host, announcements, {
                    "client_request_id": str(uuid4()), "kind": "attack",
                    "text": "Một cuộc tấn công khác không rõ thủ phạm."})
                assert named.status_code == anonymous.status_code == 200
                for notice in (named, anonymous):
                    for socket in sockets:
                        frame = await socket.event(notice.json()["event_id"])
                        assert set(frame) == {"type", "event_id", "sequence", "kind"}
                for client in (target, witness):
                    cards = (await client.get(announcements)).json()
                    assert len(cards) == 2
                    assert cards[0]["source_player"]["user_id"] == user_ids[2]
                    assert cards[1]["source_player"] is None
                    assert all(card["related_player"] is None for card in cards)
                    assert (await client.get(f"{base}/game/death-notices")).json() == []
                    assert next(item for item in (await client.get(base)).json()["members"]
                                if item["user_id"] == user_ids[1])["public_is_alive"]
                public_version += 2
                assert (await witness.get(base)).json()["version"] == public_version

                # A later explicit publication is the only way to change public status.
                announced = await post(host, f"{action_url}/announce", {
                    "client_request_id": str(uuid4()), "moderator_reason": "Đổi quyết định sau khi xét role",
                    "reveal_role": False})
                assert announced.status_code == 200
                async with session_factory()() as db:
                    public_event = await db.scalar(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id),
                        GameEvent.kind == "player.death_announced"))
                    assert public_event is not None
                for socket in sockets:
                    notice = await socket.event(str(public_event.id))
                    assert set(notice) == {"type", "event_id", "sequence", "kind"}
                assert (await witness.get(base)).json()["version"] == public_version + 1
                assert not next(item for item in (await witness.get(base)).json()["members"]
                                if item["user_id"] == user_ids[1])["public_is_alive"]
                assert len((await witness.get(f"{base}/game/death-notices")).json()) == 1
                assert next(item for item in (await host.get(f"{base}/game/players")).json()
                            if item["user_id"] == user_ids[1])["publication_disposition"] == "published"
                for source_id in (user_ids[2], None):
                    body = {"client_request_id": str(uuid4()), "kind": "death",
                            "text": "Quản trò công bố sự kiện giết ban ngày.",
                            "related_user_id": user_ids[1]}
                    if source_id:
                        body["source_user_id"] = source_id
                    public_death = await post(host, announcements, body)
                    assert public_death.status_code == 200
                    for socket in sockets:
                        frame = await socket.event(public_death.json()["event_id"])
                        assert set(frame) == {"type", "event_id", "sequence", "kind"}
                cards = (await witness.get(announcements)).json()
                assert cards[-2]["source_player"]["user_id"] == user_ids[2]
                assert cards[-1]["source_player"] is None
                assert all(card["related_player"]["user_id"] == user_ids[1]
                           for card in cards[-2:])
                assert len((await witness.get(f"{base}/game/death-notices")).json()) == 1
                public_version += 2
                assert (await post(host, f"{action_url}/actions", {
                    "client_request_id": str(uuid4()), "action": "revive",
                    "reason": "Kiểm tra trạng thái mới"})).status_code == 200
                assert next(item for item in (await host.get(f"{base}/game/players")).json()
                            if item["user_id"] == user_ids[1])["publication_disposition"] == "pending"
                assert (await witness.get(base)).json()["version"] == public_version + 1
                assert (await target.get(f"{base}/chat/context")).json()["can_send_common"] is False
                assert (await post(target, f"{base}/chat/messages",
                    chat_body("common", "Hồi sinh kín"))).status_code == 403
                assert (await post(host, f"{action_url}/announce", {
                    "client_request_id": str(uuid4()), "moderator_reason": "Cho phép công bố hồi sinh",
                    "reveal_role": False})).status_code == 200
                assert (await target.get(f"{base}/chat/context")).json()["can_send_common"] is True
                assert (await post(target, f"{base}/chat/messages",
                    chat_body("common", "Đã công bố hồi sinh"))).status_code == 200
            finally:
                for socket in sockets:
                    await socket.close()
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in user_ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())
