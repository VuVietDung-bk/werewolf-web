from uuid import UUID
import hmac
import logging
import os
import time

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.auth import (
    COOKIE_NAME, SESSION_DAYS, current_identity, current_user, lookup_session,
    new_session, now_utc, origin_allowed, require_room_access, secure_cookie,
    websocket_user,
)
from app.db.models import Session, User
from app.db.session import get_db, session_factory
from app.rooms import router as rooms_router
from app.roles import router as roles_router
from app.draft import router as draft_router
from app.icons import router as icons_router
from app.player_state import notices_router, router as player_state_router
from app.private_messages import router as private_messages_router
from app.demo import router as demo_router
from app.event_corrections import router as event_corrections_router
from app.content_revisions import router as content_revisions_router
from app.votes import router as votes_router
from app.chat import router as chat_router
from app.live import router as live_router
from app.role_requests import router as role_requests_router
from app.transcript import router as transcript_router
from app.live import hub
from app.observability import gauge, http_metrics
from app.outbox_worker import HEARTBEAT_KEY, redis_client

app = FastAPI(title="Ma Sói Web API", version="0.1.0")
# Uvicorn's default access log includes raw URL query strings (transcript search
# can contain private chat). Keep only route templates in our own access log.
logging.getLogger("uvicorn.access").disabled = True
access_logger = logging.getLogger("uvicorn.ma_soi_access")


class WebSocketPathFilter(logging.Filter):
    """Uvicorn logs the raw WebSocket URL through uvicorn.error at INFO."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (isinstance(record.msg, str) and '"WebSocket %s"' in record.msg
                and isinstance(record.args, tuple) and len(record.args) >= 2):
            record.args = (record.args[0], "[redacted]", *record.args[2:])
        return True


logging.getLogger("uvicorn.error").addFilter(WebSocketPathFilter())
app.include_router(rooms_router)
app.include_router(roles_router)
app.include_router(draft_router)
app.include_router(icons_router)
app.include_router(player_state_router)
app.include_router(notices_router)
app.include_router(private_messages_router)
app.include_router(demo_router)
app.include_router(event_corrections_router)
app.include_router(content_revisions_router)
app.include_router(votes_router)
app.include_router(chat_router)
app.include_router(live_router)
app.include_router(role_requests_router)
app.include_router(transcript_router)


class GuestInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class IdentityOutput(BaseModel):
    id: UUID
    display_name: str


@app.middleware("http")
async def check_write_origin(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not origin_allowed(
            request.headers.get("origin")
        ):
            response = Response(status_code=403, content="Origin không được phép")
        else:
            response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        status = response.status_code
        return response
    finally:
        if request.url.path != "/internal/metrics":
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            http_metrics.record(request.method, route_path,
                                status, time.perf_counter() - start)
            access_logger.info("HTTP %s %s %d", request.method, route_path, status)


@app.get("/internal/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    token = os.getenv("METRICS_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    if not token or not hmac.compare_digest(supplied, f"Bearer {token}"):
        raise HTTPException(status_code=404)
    output = http_metrics.render()
    output += gauge("ma_soi_websocket_connections", "Active room WebSocket connections in this API process",
                    sum(len(feed.connections) for feed in hub.feeds.values()))
    output += gauge("ma_soi_websocket_rooms", "Room feeds in this API process", len(hub.feeds))
    try:
        async with session_factory()() as db:
            row = (await db.execute(text("""
                SELECT (SELECT count(*) FROM outbox WHERE published_at IS NULL),
                       (SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0)
                        FROM outbox WHERE published_at IS NULL),
                       (SELECT count(*) FROM outbox WHERE published_at IS NULL AND attempts > 1),
                       pg_database_size(current_database()),
                       (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database())
            """))).one()
        output += gauge("ma_soi_postgres_up", "PostgreSQL query succeeded", 1)
        output += gauge("ma_soi_outbox_pending", "Unpublished outbox rows", row[0])
        output += gauge("ma_soi_outbox_oldest_age_seconds", "Age of oldest unpublished outbox row", float(row[1]))
        output += gauge("ma_soi_outbox_retry_pending", "Unpublished rows with multiple attempts", row[2])
        output += gauge("ma_soi_database_size_bytes", "Current PostgreSQL database size", row[3])
        output += gauge("ma_soi_database_connections", "Current PostgreSQL connections", row[4])
    except Exception:
        output += gauge("ma_soi_postgres_up", "PostgreSQL query succeeded", 0)
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        client = redis_client(redis_url)
        try:
            await client.ping()
            heartbeat = await client.get(HEARTBEAT_KEY)
            age = max(0.0, time.time() - float(heartbeat)) if heartbeat else -1
            output += gauge("ma_soi_redis_up", "Redis query succeeded, or -1 when disabled", 1)
            output += gauge("ma_soi_outbox_worker_heartbeat_age_seconds", "Age of worker heartbeat, or -1 when absent", age)
        except Exception:
            output += gauge("ma_soi_redis_up", "Redis query succeeded, or -1 when disabled", 0)
            output += gauge("ma_soi_outbox_worker_heartbeat_age_seconds", "Age of worker heartbeat, or -1 when absent", -1)
        finally:
            await client.aclose()
    else:
        output += gauge("ma_soi_redis_up", "Redis query succeeded, or -1 when disabled", -1)
        output += gauge("ma_soi_outbox_worker_heartbeat_age_seconds", "Age of worker heartbeat, or -1 when absent", -1)
    return Response(content=output, media_type="text/plain; version=0.0.4")


@app.get("/api/health/live", tags=["health"])
async def live() -> dict[str, str]:
    """Liveness does not depend on services that may be restarting."""
    return {"status": "ok"}


@app.post("/api/auth/guest", response_model=IdentityOutput, tags=["auth"])
async def guest_login(
    body: GuestInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> IdentityOutput:
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Tên hiển thị không được để trống")
    existing = await lookup_session(db, request.cookies.get(COOKIE_NAME))
    if existing is not None:
        return IdentityOutput(id=existing[0].id, display_name=existing[0].display_name)
    user = User(display_name=name)
    db.add(user)
    await db.flush()
    session, token = new_session(user.id)
    db.add(session)
    await db.commit()
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True, secure=secure_cookie(), samesite="lax", path="/",
    )
    return IdentityOutput(id=user.id, display_name=user.display_name)


@app.get("/api/auth/me", response_model=IdentityOutput, tags=["auth"])
async def me(user: User = Depends(current_user)) -> IdentityOutput:
    return IdentityOutput(id=user.id, display_name=user.display_name)


@app.post("/api/auth/logout", status_code=204, tags=["auth"])
async def logout(
    response: Response,
    identity: tuple[User, Session] = Depends(current_identity),
    db: AsyncSession = Depends(get_db),
) -> None:
    session = identity[1]
    session.revoked_at = now_utc()
    await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@app.get("/api/rooms/{room_id}/access", tags=["rooms"])
async def room_access(
    room_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    return {"is_host": await require_room_access(db, room_id, user.id)}


@app.websocket("/api/ws/session")
async def session_websocket(websocket: WebSocket) -> None:
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    user = await websocket_user(websocket)
    if user is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    await websocket.send_json({"user_id": str(user.id)})
    await websocket.close()
