"""Repeatable, isolated 500-player HTTP/WebSocket load scenario.

Run only against a migrated database named ma_soi_load. The script seeds valid
playing rooms directly, then exercises the public HTTP and WebSocket APIs.
"""

import argparse
import asyncio
import json
import os
import platform
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import websockets
from sqlalchemy import func, insert, select, text

from app.auth import POSTGAME_POLICY_VERSION, new_session, now_utc
from app.db.models import (ChatMessage, GameEvent, Outbox, PlayerState, RoleAssignment,
                           RoleSnapshot, Room, RoomMember, Session, User)
from app.db.session import session_factory

ORIGIN = "http://localhost:5173"


@dataclass
class Actor:
    user_id: UUID
    cookie: str


@dataclass
class ScenarioRoom:
    room_id: UUID
    host: Actor
    players: list[Actor]
    vote_id: str | None = None


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    low = int(index)
    return round(ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (index - low), 3)


def stats(values: list[float]) -> dict:
    return {"n": len(values), "p50_ms": percentile(values, 50),
            "p95_ms": percentile(values, 95), "p99_ms": percentile(values, 99),
            "max_ms": round(max(values), 3) if values else None}


def machine() -> dict:
    result = {"platform": platform.platform(), "logical_cpus": os.cpu_count()}
    for key, path in (("cpu_quota", "/sys/fs/cgroup/cpu.max"),
                      ("memory_limit_bytes", "/sys/fs/cgroup/memory.max")):
        try:
            result[key] = Path(path).read_text().strip()
        except OSError:
            result[key] = "unknown"
    return result


async def seed(room_count: int, player_count: int) -> list[ScenarioRoom]:
    rooms = []
    people = []
    sessions = []
    room_rows = []
    members = []
    snapshots = []
    assignments = []
    states = []
    stamp = now_utc()
    for index in range(room_count):
        room_id, role_id = uuid4(), uuid4()

        def actor(label: str) -> Actor:
            user_id = uuid4()
            session, token = new_session(user_id)
            people.append({"id": user_id, "display_name": label})
            sessions.append({"id": uuid4(), "user_id": user_id,
                             "token_hash": session.token_hash, "expires_at": session.expires_at})
            return Actor(user_id, token)

        host = actor(f"Load host {index + 1}")
        players = [actor(f"Load {index + 1}-{seat + 1}") for seat in range(player_count)]
        rooms.append(ScenarioRoom(room_id, host, players))
        room_rows.append({"id": room_id, "owner_user_id": host.user_id,
                          "code": f"L{uuid4().hex[:11]}{index:04x}", "name": f"Load room {index + 1}",
                          "seat_limit": player_count, "status": "playing",
                          "phase_type": "day", "round_number": 0})
        role = {"id": str(role_id), "name": "Dân thử tải", "description": "Thảo luận ban ngày",
                "faction_label": "Làng", "seer_result": "Thiện", "team_chat_label": None,
                "quantity": player_count, "role_kind": "passive", "request_phases": [],
                "request_when_dead": False, "host_contact": {"can_initiate": False,
                "initiate_phases": [], "reply_allowed": True, "when_dead": False},
                "icon_key": "auto", "icon_id": None}
        snapshots.append({"room_id": room_id, "set_name": "Thử tải",
                          "definitions": [role], "distribution_mode": "random", "settings": {}})
        for seat, player in enumerate(players, 1):
            members.append({"room_id": room_id, "user_id": player.user_id,
                            "seat_number": seat, "consent_version": POSTGAME_POLICY_VERSION,
                            "consented_at": stamp})
            assignments.append({"room_id": room_id, "user_id": player.user_id,
                                "role_id": role_id})
            states.append({"room_id": room_id, "user_id": player.user_id,
                           "is_alive": True, "public_is_alive": True,
                           "publication_disposition": "published"})
    async with session_factory()() as db:
        async with db.begin():
            for model, rows in ((User, people), (Session, sessions), (Room, room_rows),
                                (RoomMember, members), (RoleSnapshot, snapshots),
                                (RoleAssignment, assignments), (PlayerState, states)):
                await db.execute(insert(model), rows)
    return rooms


async def request(client: httpx.AsyncClient, actor: Actor, path: str, body: dict) -> tuple[int, dict, float]:
    began = time.monotonic()
    try:
        response = await client.post(path, json=body, headers={
            "Origin": ORIGIN, "Cookie": f"ma_soi_session={actor.cookie}"})
        return response.status_code, response.json() if response.content else {}, (time.monotonic() - began) * 1000
    except (httpx.HTTPError, ValueError) as error:
        return 0, {"error": type(error).__name__}, (time.monotonic() - began) * 1000


async def run(args) -> dict:
    if not os.environ.get("DATABASE_URL", "").split("/")[-1].split("?")[0] == "ma_soi_load":
        raise SystemExit("Refusing to seed: DATABASE_URL must name ma_soi_load")
    rooms = await seed(args.rooms, args.players)
    base = args.api.rstrip("/")
    ws_base = base.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    observations: dict[str, list[float | None]] = {}
    submitted: dict[str, tuple[float, int]] = {}
    http_chat: list[float] = []
    host_latency: list[float] = []
    ballot_latency: list[float] = []
    failures: Counter = Counter()
    ws_errors: Counter = Counter()
    sockets = []
    readers = []

    async def read_socket(socket, seat: int):
        try:
            async for raw in socket:
                frame = json.loads(raw)
                if frame.get("type") == "game.event" and frame.get("kind") == "chat.sent":
                    event_id = frame["event_id"]
                    slots = observations.setdefault(event_id, [None] * args.players)
                    if slots[seat] is not None:
                        ws_errors["duplicate_frame"] += 1
                    else:
                        slots[seat] = time.monotonic()
        except Exception as error:
            ws_errors[type(error).__name__] += 1

    async with httpx.AsyncClient(limits=limits, timeout=30) as client:
        connect_gate = asyncio.Semaphore(40)

        async def connect(room: ScenarioRoom, seat: int):
            async with connect_gate:
                player = room.players[seat]
                socket = await websockets.connect(f"{ws_base}/api/rooms/{room.room_id}/live?after_sequence=0",
                                                   origin=ORIGIN,
                                                   additional_headers={"Cookie": f"ma_soi_session={player.cookie}"},
                                                   compression=None, max_size=65536, open_timeout=30)
                frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=30))
                if frame.get("type") != "ready":
                    raise RuntimeError("WebSocket did not become ready")
                sockets.append(socket)
                readers.append(asyncio.create_task(read_socket(socket, seat)))

        try:
            await asyncio.gather(*(connect(room, seat) for room in rooms for seat in range(args.players)))
            for room in rooms:
                status, body, _ = await request(client, room.host,
                    f"{base}/api/rooms/{room.room_id}/votes", {"client_request_id": str(uuid4())})
                if status != 200:
                    raise RuntimeError(f"Cannot open vote in room {room.room_id}: {status} {body}")
                room.vote_id = body["vote_round_id"]

            print(f"ready: {args.rooms} rooms, {args.rooms * args.players} player sockets; running {args.duration}s", flush=True)
            start = time.monotonic()
            pending: set[asyncio.Task] = set()
            all_tasks: list[asyncio.Task] = []
            gate = asyncio.Semaphore(160)

            async def chat(number: int, began: float):
                async with gate:
                    room_index = number % args.rooms
                    room = rooms[room_index]
                    seat = (number // args.rooms) % args.players
                    status, body, elapsed = await request(client, room.players[seat],
                        f"{base}/api/rooms/{room.room_id}/chat/messages",
                        {"client_request_id": str(uuid4()), "channel": "common",
                         "text": f"load-{number}"})
                    http_chat.append(elapsed)
                    if status == 200:
                        submitted[body["event_id"]] = (began, room_index)
                    else:
                        failures[f"chat_{status}"] += 1

            async def burst():
                jobs = []
                for number in range(args.burst):
                    room = rooms[(number // 2) % args.rooms]
                    if number % 2 == 0:
                        seat = (number // 2 // args.rooms) % args.players
                        actor = room.players[seat]
                        path = f"{base}/api/rooms/{room.room_id}/votes/{room.vote_id}/ballots"
                        body = {"client_request_id": str(uuid4()), "choice": "skip"}
                        kind = "ballot"
                    else:
                        actor = room.host
                        path = f"{base}/api/rooms/{room.room_id}/game/events/announcements"
                        body = {"client_request_id": str(uuid4()), "text": f"Load notice {number}"}
                        kind = "host"
                    jobs.append((kind, actor, path, body))
                launched = time.monotonic()

                async def issue(number: int, actor: Actor, path: str, body: dict):
                    due = launched + number * args.burst_span / max(1, len(jobs))
                    if due > time.monotonic():
                        await asyncio.sleep(due - time.monotonic())
                    return await request(client, actor, path, body)

                answers = await asyncio.gather(*(issue(number, actor, path, body)
                                                 for number, (_, actor, path, body) in enumerate(jobs)))
                for (kind, _, _, _), (status, body, elapsed) in zip(jobs, answers):
                    (host_latency if kind == "host" else ballot_latency).append(elapsed)
                    if status != 200:
                        failures[f"{kind}_{status}"] += 1
                return round(time.monotonic() - launched, 3)

            burst_task = None
            count = int(args.duration * args.rate)
            for number in range(count):
                if burst_task is None and time.monotonic() - start >= args.duration / 2:
                    burst_task = asyncio.create_task(burst())
                due = start + number / args.rate
                if due > time.monotonic():
                    await asyncio.sleep(due - time.monotonic())
                began = time.monotonic()
                task = asyncio.create_task(chat(number, began))
                pending.add(task)
                all_tasks.append(task)
                task.add_done_callback(pending.discard)
                if number and number % max(1, args.rate * 60) == 0:
                    print(f"minute {number // (args.rate * 60)}: sent {number}, accepted {len(submitted)}, pending {len(pending)}", flush=True)
            if burst_task is None:
                burst_task = asyncio.create_task(burst())
            await asyncio.gather(*all_tasks)
            burst_wall = await burst_task
            # Allow the polling feed and Redis worker to deliver the last commit.
            deadline = time.monotonic() + args.drain_seconds
            while time.monotonic() < deadline:
                if all(sum(value is not None for value in observations.get(event_id, [])) == args.players
                       for event_id in submitted):
                    break
                await asyncio.sleep(0.5)
        finally:
            await asyncio.gather(*(socket.close() for socket in sockets), return_exceptions=True)
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)

    delivery = []
    complete = []
    missing = 0
    for event_id, (began, _) in submitted.items():
        seen = observations.get(event_id, [None] * args.players)
        times = [arrival for arrival in seen if arrival is not None]
        missing += args.players - len(times)
        delivery.extend(max(0, (arrival - began) * 1000) for arrival in times)
        if len(times) == args.players:
            complete.append(max(0, (max(times) - began) * 1000))

    ids = [room.room_id for room in rooms]
    async with session_factory()() as db:
        chat_ids = set((await db.scalars(select(ChatMessage.event_id).where(
            ChatMessage.room_id.in_(ids)))).all())
        event_counts = dict((await db.execute(select(GameEvent.kind, func.count()).where(
            GameEvent.room_id.in_(ids)).group_by(GameEvent.kind))).all())
        outbox_count = await db.scalar(select(func.count()).select_from(Outbox).join(
            GameEvent, GameEvent.id == Outbox.event_id).where(GameEvent.room_id.in_(ids)))
        database_bytes = await db.scalar(text("SELECT pg_database_size(current_database())"))
    result = {
        "scenario": {"rooms": args.rooms, "players_per_room": args.players,
                     "player_sockets": args.rooms * args.players, "duration_seconds": args.duration,
                     "target_chat_per_second": args.rate, "chat_offered": count,
                     "burst_commands": args.burst, "burst_span_seconds": args.burst_span,
                     "burst_launch_to_complete_seconds": burst_wall},
        "machine": machine(),
        "http_chat": stats(http_chat), "host_commands": stats(host_latency),
        "ballots": stats(ballot_latency), "websocket_delivery": stats(delivery),
        "whole_message_delivery": stats(complete),
        "delivery_under_2s_percent": round(100 * sum(value <= 2000 for value in delivery) / len(delivery), 3) if delivery else 0,
        "host_under_500ms_percent": round(100 * sum(value <= 500 for value in host_latency) / len(host_latency), 3) if host_latency else 0,
        "http_successful_chat": len(submitted), "postgres_chat_events": len(chat_ids),
        "acknowledged_chat_missing_from_db": len(set(map(UUID, submitted)) - chat_ids),
        "websocket_missing_deliveries": missing, "websocket_duplicate_frames": ws_errors["duplicate_frame"],
        "event_counts": event_counts, "outbox_count": outbox_count,
        "outbox_missing_events": sum(event_counts.values()) - outbox_count,
        "database_bytes": database_bytes, "failures": dict(failures),
        "websocket_errors": dict(ws_errors),
    }
    result["meets_targets"] = (result["http_successful_chat"] == count
        and result["acknowledged_chat_missing_from_db"] == 0
        and result["outbox_missing_events"] == 0
        and missing == 0 and result["delivery_under_2s_percent"] >= 99
        and result["host_under_500ms_percent"] >= 95 and not failures and not ws_errors)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument("--rooms", type=int, default=20)
    parser.add_argument("--players", type=int, default=25)
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--rate", type=int, default=25)
    parser.add_argument("--burst", type=int, default=50)
    parser.add_argument("--burst-span", type=float, default=1.0,
                        help="Time window for launching burst commands; 0 means all at once")
    parser.add_argument("--drain-seconds", type=int, default=10)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not (1 <= args.rooms <= 100 and 2 <= args.players <= 30 and args.duration > 0
            and args.rate > 0 and args.burst >= 0 and args.burst_span >= 0):
        parser.error("Invalid scenario size")
    result = asyncio.run(run(args))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if not result["meets_targets"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
