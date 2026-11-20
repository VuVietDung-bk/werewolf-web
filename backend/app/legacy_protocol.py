"""Numeric read compatibility is restricted to the host or consented postgame history."""
from fastapi import HTTPException
from app.auth import require_room_access
from app.db.models import Room

async def require_legacy_reader(db,room_id,user):
    host=await require_room_access(db,room_id,user.id)
    room=await db.get(Room,room_id)
    if not host and room.status!='finished':
        raise HTTPException(status_code=426,detail={'code':'client_upgrade_required',
            'message':'Cập nhật ứng dụng để dùng lịch sử kênh v2.'})
    return host
