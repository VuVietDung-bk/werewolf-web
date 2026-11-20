"""Test-only transport for retained feed-domain regressions; never mounted in app.main."""
from uuid import UUID
from fastapi import FastAPI, HTTPException, WebSocket
from app.auth import COOKIE_NAME, lookup_session, origin_allowed, require_room_access
from app.db.models import Room
from app.db.session import session_factory
from app.live import Connection, hub

service_app=FastAPI()
@service_app.websocket('/api/rooms/{room_id}/live')
async def numeric_feed_service(websocket: WebSocket, room_id: UUID) -> None:
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    async with session_factory()() as db:
        identity = await lookup_session(db, websocket.cookies.get(COOKIE_NAME))
        if identity is None:
            await websocket.close(code=4401)
            return
        user, session = identity
        try:
            await require_room_access(db, room_id, user.id)
        except HTTPException as error:
            await websocket.close(code=4404 if error.status_code == 404 else 4403)
            return
        room = await db.get(Room, room_id)
        raw_cursor = websocket.query_params.get("after_sequence")
        if raw_cursor is not None and (not raw_cursor.isascii() or not raw_cursor.isdecimal()
                                       or len(raw_cursor) > 20):
            await websocket.close(code=4400)
            return
        cursor = min(int(raw_cursor), room.version) if raw_cursor is not None else room.version
        connection = Connection(websocket=websocket, user_id=user.id,
                                session_id=session.id, cursor=cursor)
        host_id = room.owner_user_id
    await websocket.accept()
    await websocket.send_json({"type": "ready", "cursor": cursor})
    await hub.add(room_id, host_id, connection)
    try:
        while True:
            data = await websocket.receive()
            if data["type"] == "websocket.disconnect":
                break
            # Notifications are server-to-client only; do not accept client commands here.
            await websocket.close(code=1003)
            break
    finally:
        await hub.remove(room_id, connection)
