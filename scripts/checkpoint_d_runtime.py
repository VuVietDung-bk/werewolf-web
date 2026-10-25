"""Exercise API restart and Redis/outbox interruption on ma_soi_checkpoint_d only."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import websockets
from sqlalchemy import func, select

from app.db.models import GameEvent, Outbox, Room, RoomMember
from app.db.session import session_factory

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8012"
ORIGIN = "http://localhost:5173"
PYTHON = ROOT / ".venv/bin/python"


def check(response: httpx.Response, status: int = 200):
    if response.status_code != status:
        raise AssertionError(f"{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:500]}")
    return response.json() if response.content else None


def post(client: httpx.AsyncClient, path: str, body: dict):
    return client.post(path, json=body, headers={"Origin": ORIGIN})


def command(**values):
    return {"client_request_id": str(uuid4()), **values}


def compose(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def stop(process: subprocess.Popen | None):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def ready():
    async with httpx.AsyncClient(timeout=2) as client:
        for _ in range(80):
            try:
                if (await client.get(f"{API}/api/health/live")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.25)
    raise AssertionError("API did not start")


async def db_snapshot(room_id: str):
    async with session_factory()() as db:
        room = await db.get(Room, room_id)
        return {
            "status": room.status,
            "version": room.version,
            "members": await db.scalar(select(func.count()).select_from(RoomMember).where(RoomMember.room_id == room_id)),
            "events": await db.scalar(select(func.count()).select_from(GameEvent).where(GameEvent.room_id == room_id)),
            "outbox_pending": await db.scalar(select(func.count()).select_from(Outbox).join(GameEvent).where(
                GameEvent.room_id == room_id, Outbox.published_at.is_(None))),
        }


def launch_api(env, log):
    return subprocess.Popen([str(PYTHON), "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
                             "--host", "127.0.0.1", "--port", "8012", "--ws", "websockets-sansio",
                             "--no-access-log"], cwd=ROOT, env=env, stdout=log, stderr=log)


def launch_worker(env, log):
    return subprocess.Popen([str(PYTHON), "-m", "app.outbox_worker"], cwd=ROOT, env=env,
                            stdout=log, stderr=log)


async def receive_event(socket, event_id: str):
    async def wait():
        while True:
            frame = json.loads(await socket.recv())
            if frame.get("event_id") == event_id:
                return frame
    return await asyncio.wait_for(wait(), timeout=10)


async def run():
    env = os.environ.copy()
    if env.get("DATABASE_URL", "").split("/")[-1].split("?")[0] != "ma_soi_checkpoint_d":
        raise SystemExit("DATABASE_URL must name ma_soi_checkpoint_d")
    env.update(APP_ENV="local", APP_ORIGINS=ORIGIN, REDIS_URL="redis://127.0.0.1:6380/0",
               PYTHONPATH=str(ROOT / "backend"))
    api = worker = None
    redis_stopped = False
    with open("/tmp/ma-soi-d2-api.log", "w") as api_log, open("/tmp/ma-soi-d2-worker.log", "w") as worker_log:
        try:
            api = launch_api(env, api_log)
            worker = launch_worker(env, worker_log)
            await ready()
            async with (httpx.AsyncClient(base_url=API, timeout=10) as host,
                        httpx.AsyncClient(base_url=API, timeout=10) as player,
                        httpx.AsyncClient(base_url=API, timeout=10) as second_player):
                host_id = check(await post(host, "/api/auth/guest", {"display_name": "D2 quản trò"}))["id"]
                player_id = check(await post(player, "/api/auth/guest", {"display_name": "D2 người chơi"}))["id"]
                second_player_id = check(await post(second_player, "/api/auth/guest", {"display_name": "D2 người chơi thứ hai"}))["id"]
                room = check(await post(host, "/api/rooms", {"name": "Kiểm tra D2", "seat_limit": 2}), 201)
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                check(await post(player, "/api/rooms/join", {"code": room["code"],
                                                               "accept_postgame_transcript": True}))
                check(await post(second_player, "/api/rooms/join", {"code": room["code"],
                                                                      "accept_postgame_transcript": True}))
                check(await post(host, f"{base}/roles", {"name": "Dân", "description": "Thử phục hồi",
                    "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "passive", "quantity": 2}), 201)
                check(await host.patch(f"{base}/settings", json={"name": "Kiểm tra D2", "seat_limit": 2,
                    "registration_open": False}, headers={"Origin": ORIGIN}))
                check(await post(host, f"{base}/roles/distribute-random", {}))
                check(await post(host, f"{base}/game/start", {}))
                before = await db_snapshot(room_id)
                socket_url = f"ws://127.0.0.1:8012{base}/live"
                cookie = f"ma_soi_session={player.cookies.get('ma_soi_session')}"
                async with websockets.connect(f"{socket_url}?after_sequence=0", origin=ORIGIN,
                                               additional_headers={"Cookie": cookie}) as socket:
                    assert json.loads(await socket.recv())["type"] == "ready"
                    first = check(await post(player, f"{base}/chat/messages", command(
                        channel="common", text="D2 trước khi ngắt")))
                    first_frame = await receive_event(socket, first["event_id"])
                    stop(worker)
                    worker = None
                    compose("stop", "redis")
                    redis_stopped = True
                    second = check(await post(player, f"{base}/chat/messages", command(
                        channel="common", text="D2 khi Redis và worker ngắt")))
                    second_frame = await receive_event(socket, second["event_id"])
                    interrupted = await db_snapshot(room_id)
                    assert interrupted["outbox_pending"] > 0
                    stop(api)
                    api = None
                api = launch_api(env, api_log)
                await ready()
                restored_room = check(await player.get(base))
                assert restored_room["status"] == "playing"
                assert {item["user_id"] for item in restored_room["members"]} == {player_id, second_player_id}
                third = check(await post(player, f"{base}/chat/messages", command(
                    channel="common", text="D2 sau khi API khởi động lại")))
                async with websockets.connect(f"{socket_url}?after_sequence={first_frame['sequence']}",
                                               origin=ORIGIN, additional_headers={"Cookie": cookie}) as socket:
                    assert json.loads(await socket.recv())["type"] == "ready"
                    replay = [await receive_event(socket, event_id) for event_id in
                              (second["event_id"], third["event_id"])]
                    assert [item["sequence"] for item in replay] == sorted({
                        second_frame["sequence"], replay[1]["sequence"]})
                compose("start", "redis")
                redis_stopped = False
                worker = launch_worker(env, worker_log)
                for _ in range(80):
                    after = await db_snapshot(room_id)
                    if after["outbox_pending"] == 0:
                        break
                    await asyncio.sleep(.25)
                assert after["outbox_pending"] == 0
                assert after["members"] == before["members"] == 2
                assert after["events"] == interrupted["events"] + 1
                result = {"room_id": room_id, "host_id": host_id, "player_id": player_id,
                          "second_player_id": second_player_id,
                          "before": before, "during_outage": interrupted, "after": after,
                          "replayed_sequences": [item["sequence"] for item in replay],
                          "redis_offline_delivery_sequence": second_frame["sequence"]}
                report = ROOT / "docs/step-9/checkpoint-d-runtime.json"
                report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        finally:
            stop(api)
            stop(worker)
            if redis_stopped:
                compose("start", "redis")


if __name__ == "__main__":
    asyncio.run(run())
