"""Room-owned role icons, normalized to static PNG before persistence."""

from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_access
from app.db.events import append_event
from app.db.models import RoleIcon, User
from app.db.session import get_db
from app.roles import locked_host_room

router = APIRouter(prefix="/api/rooms/{room_id}/icons", tags=["roles"])
MAX_UPLOAD_BYTES = 1024 * 1024
MAX_EDGE = 512
ACCEPTED_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


class IconOutput(BaseModel):
    id: UUID
    url: str
    width: int
    height: int


def normalize_icon(raw: bytes, declared_type: str) -> tuple[bytes, int, int]:
    try:
        with Image.open(BytesIO(raw)) as opened:
            if opened.format != ACCEPTED_FORMATS[declared_type]:
                raise HTTPException(status_code=415, detail="Nội dung ảnh không khớp định dạng khai báo")
            width, height = opened.size
            if not (1 <= width <= MAX_EDGE and 1 <= height <= MAX_EDGE):
                raise HTTPException(status_code=413, detail="Icon tối đa 512 × 512 px")
            if getattr(opened, "is_animated", False) or getattr(opened, "n_frames", 1) != 1:
                raise HTTPException(status_code=415, detail="Chỉ nhận ảnh tĩnh")
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            # A fresh image strips EXIF, comments and other embedded metadata.
            clean = Image.new("RGBA", image.size)
            clean.paste(image)
            output = BytesIO()
            clean.save(output, format="PNG", optimize=True)
            return output.getvalue(), clean.width, clean.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(status_code=415, detail="Không đọc được ảnh hợp lệ") from None


@router.post("", response_model=IconOutput, status_code=201)
async def upload_icon(room_id: UUID, request: Request, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)) -> IconOutput:
    await locked_host_room(db, room_id, user.id)
    declared_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared_type not in ACCEPTED_FORMATS:
        raise HTTPException(status_code=415, detail="Chỉ nhận PNG, WebP hoặc JPEG")
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Icon tối đa 1 MiB")
    if not payload:
        raise HTTPException(status_code=422, detail="Tệp ảnh rỗng")
    image_bytes, width, height = normalize_icon(bytes(payload), declared_type)
    icon = RoleIcon(room_id=room_id, image_bytes=image_bytes, width=width, height=height)
    db.add(icon)
    await db.flush()
    await append_event(db, room_id, user.id, "role.icon_uploaded", "host",
                       {"icon_id": str(icon.id), "width": width, "height": height})
    await db.commit()
    return IconOutput(id=icon.id, url=f"/api/rooms/{room_id}/icons/{icon.id}",
                      width=width, height=height)


@router.get("/{icon_id}")
async def get_icon(room_id: UUID, icon_id: UUID, user: User = Depends(current_user),
                   db: AsyncSession = Depends(get_db)) -> Response:
    await require_room_access(db, room_id, user.id)
    icon = await db.get(RoleIcon, icon_id)
    if icon is None or icon.room_id != room_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy icon")
    return Response(icon.image_bytes, media_type="image/png", headers={
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })
