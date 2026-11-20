"""Domain regression helpers for numeric readers retained for the host.

Player HTTP callers are tested separately against the 426 boundary and v2. These
helpers exercise source ACL/pagination as internal services, not a public API shim.
"""
from urllib.parse import parse_qs, urlsplit
from uuid import UUID
import httpx
from fastapi import HTTPException
from app.auth import COOKIE_NAME, lookup_session, require_room_access
from app.chat import read_chat_page, read_chat_context
from app.private_messages import messages_for_player
from app.role_requests import read_role_requests
from app.db.session import session_factory

async def service_get(client,url,**kwargs):
    path=urlsplit(str(url)).path
    if not (path.endswith(('/chat/messages','/chat/timeline','/chat/context','/game/private-messages','/role-requests'))):
        return await client.get(url,**kwargs)
    parts=path.split('/');room_id=UUID(parts[3]);params={key:values[-1] for key,values in parse_qs(urlsplit(str(url)).query).items()}
    params.update(kwargs.get('params') or {})
    integer=lambda key,default=None:int(params[key]) if key in params else default
    if not 1<=integer('limit',30)<=50:return await client.get(url,**kwargs)
    try:
        async with session_factory()() as db:
            identity=await lookup_session(db,client.cookies.get(COOKIE_NAME))
            if not identity:raise HTTPException(status_code=401,detail='No session')
            user=identity[0]
            if path.endswith('/chat/context'):
                result=await read_chat_context(room_id,user,db)
            elif path.endswith('/game/private-messages'):
                if await require_room_access(db,room_id,user.id):raise HTTPException(status_code=403,detail='Host uses player inbox')
                result=await messages_for_player(db,room_id,user.id)
            elif path.endswith('/role-requests'):
                result=await read_role_requests(room_id,integer('before_sequence'),integer('after_sequence'),integer('after_updated_sequence'),integer('limit',30),user,db)
            else:
                result=await read_chat_page(db,user,room_id,params['channel'],integer('before_sequence'),integer('after_sequence'),integer('after_updated_sequence'),
                    integer('limit',30),params.get('team_label'),UUID(params['participant_user_id']) if params.get('participant_user_id') else None,
                    include_private=path.endswith('/timeline'))
            body=[item.model_dump(mode='json') for item in result] if isinstance(result,list) else result.model_dump(mode='json')
            return httpx.Response(200,json=body)
    except HTTPException as error:return httpx.Response(error.status_code,json={'detail':error.detail})
