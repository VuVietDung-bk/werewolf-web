"""Explicitly enabled local demo account switching."""

import hmac
import json
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE_NAME, SESSION_DAYS, new_session, secure_cookie
from app.db.models import Room, RoomMember, User
from app.db.session import get_db

router = APIRouter(prefix="/api/dev/demo", tags=["local demo"])
MANIFEST = Path(__file__).resolve().parents[2] / ".local-demo.json"


class DemoActor(BaseModel):
    id: UUID
    name: str
    kind: str
    seat: int | None = None


class DemoInfo(BaseModel):
    room_id: UUID
    room_name: str
    actors: list[DemoActor]


class SwitchInput(BaseModel):
    actor_id: UUID


def read_demo(x_demo_key: str | None) -> dict:
    if os.getenv("APP_ENV") != "local" or os.getenv("ENABLE_DEMO_MODE") != "1":
        raise HTTPException(status_code=404, detail="Chế độ trải nghiệm chưa bật")
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Chưa tạo phòng trải nghiệm") from None
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=409, detail="Cấu hình phòng trải nghiệm bị lỗi")
    key = manifest.get("key")
    if not isinstance(key, str) or not x_demo_key or not hmac.compare_digest(key, x_demo_key):
        raise HTTPException(status_code=403, detail="Mã trải nghiệm không hợp lệ")
    return manifest


async def demo_info(db: AsyncSession, manifest: dict) -> DemoInfo:
    try:
        room_id = UUID(manifest["room_id"])
        actors = [DemoActor.model_validate(actor) for actor in manifest["actors"]]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=409, detail="Cấu hình phòng trải nghiệm bị lỗi") from None
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Phòng trải nghiệm không còn tồn tại")
    if not actors or len({actor.id for actor in actors}) != len(actors):
        raise HTTPException(status_code=409, detail="Danh sách tài khoản trải nghiệm bị lỗi")
    for actor in actors:
        if await db.get(User, actor.id) is None:
            raise HTTPException(status_code=409, detail="Tài khoản trải nghiệm không còn tồn tại")
        if actor.kind == "host":
            if actor.id != room.owner_user_id:
                raise HTTPException(status_code=409, detail="Quản trò trải nghiệm không khớp")
        elif actor.kind == "player":
            member = await db.get(RoomMember, (room_id, actor.id))
            if member is None or member.left_at is not None:
                raise HTTPException(status_code=409, detail="Người chơi trải nghiệm không còn trong phòng")
        else:
            raise HTTPException(status_code=409, detail="Loại tài khoản trải nghiệm không hợp lệ")
    return DemoInfo(room_id=room_id, room_name=room.name, actors=actors)


@router.get("", response_model=DemoInfo)
async def get_demo(x_demo_key: str | None = Header(default=None),
                   db: AsyncSession = Depends(get_db)) -> DemoInfo:
    return await demo_info(db, read_demo(x_demo_key))


@router.post("/switch", response_model=DemoActor)
async def switch_demo(body: SwitchInput, response: Response,
                      x_demo_key: str | None = Header(default=None),
                      db: AsyncSession = Depends(get_db)) -> DemoActor:
    info = await demo_info(db, read_demo(x_demo_key))
    actor = next((item for item in info.actors if item.id == body.actor_id), None)
    if actor is None:
        raise HTTPException(status_code=403, detail="Tài khoản không thuộc phòng trải nghiệm")
    session, token = new_session(actor.id)
    db.add(session)
    await db.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_DAYS * 24 * 60 * 60,
                        httponly=True, secure=secure_cookie(), samesite="lax", path="/")
    return actor
