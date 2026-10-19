from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    COOKIE_NAME, SESSION_DAYS, current_identity, current_user, lookup_session,
    new_session, now_utc, origin_allowed, require_room_access, secure_cookie,
    websocket_user,
)
from app.db.models import Session, User
from app.db.session import get_db
from app.rooms import router as rooms_router
from app.roles import router as roles_router
from app.draft import router as draft_router
from app.icons import router as icons_router
from app.player_state import router as player_state_router

app = FastAPI(title="Ma Sói Web API", version="0.1.0")
app.include_router(rooms_router)
app.include_router(roles_router)
app.include_router(draft_router)
app.include_router(icons_router)
app.include_router(player_state_router)


class GuestInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class IdentityOutput(BaseModel):
    id: UUID
    display_name: str


@app.middleware("http")
async def check_write_origin(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not origin_allowed(
        request.headers.get("origin")
    ):
        return Response(status_code=403, content="Origin không được phép")
    return await call_next(request)


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
