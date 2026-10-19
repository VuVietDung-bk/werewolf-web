"""Opaque cookie sessions and database-backed access checks."""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Room, RoomMember, Session, User
from app.db.session import get_db, session_factory

COOKIE_NAME = "ma_soi_session"
SESSION_DAYS = 30
POSTGAME_POLICY_VERSION = "postgame-full-v1"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session(user_id: UUID) -> tuple[Session, str]:
    token = secrets.token_urlsafe(32)
    return Session(user_id=user_id, token_hash=token_hash(token),
                   expires_at=now_utc() + timedelta(days=SESSION_DAYS)), token


async def lookup_session(db: AsyncSession, token: str | None) -> tuple[User, Session] | None:
    if not token:
        return None
    result = await db.execute(
        select(User, Session).join(Session, Session.user_id == User.id).where(
            Session.token_hash == token_hash(token),
            Session.expires_at > now_utc(),
            Session.revoked_at.is_(None),
        )
    )
    return result.one_or_none()


async def current_identity(
    request: Request, db: AsyncSession = Depends(get_db)
) -> tuple[User, Session]:
    identity = await lookup_session(db, request.cookies.get(COOKIE_NAME))
    if identity is None:
        raise HTTPException(status_code=401, detail="Phiên không hợp lệ hoặc đã hết hạn")
    return identity


async def current_user(identity: tuple[User, Session] = Depends(current_identity)) -> User:
    return identity[0]


async def websocket_user(websocket: WebSocket) -> User | None:
    async with session_factory()() as db:
        identity = await lookup_session(db, websocket.cookies.get(COOKIE_NAME))
        return identity[0] if identity else None


def allowed_origins() -> set[str]:
    default = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000"
    )
    return {part.strip().rstrip("/") for part in os.getenv("APP_ORIGINS", default).split(",") if part.strip()}


def origin_allowed(origin: str | None) -> bool:
    return bool(origin and origin in allowed_origins())


def secure_cookie() -> bool:
    return os.getenv("APP_ENV", "production") != "local"


async def require_room_access(db: AsyncSession, room_id: UUID, user_id: UUID) -> bool:
    """Return host status; reject users outside the room or who have left."""
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")
    if room.owner_user_id == user_id:
        return True
    member = await db.get(RoomMember, (room_id, user_id))
    if member is None or member.left_at is not None or member.consent_version != POSTGAME_POLICY_VERSION:
        raise HTTPException(status_code=403, detail="Không có quyền vào phòng")
    return False


async def require_room_host(db: AsyncSession, room_id: UUID, user_id: UUID) -> None:
    is_host = await require_room_access(db, room_id, user_id)
    if not is_host:
        raise HTTPException(status_code=403, detail="Chỉ quản trò được thực hiện")
