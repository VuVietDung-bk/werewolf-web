"""Opaque chat snapshots and changes. Source ordinals remain internal."""
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_room_host
from app.chat import ChatMessageOutput, read_chat_page, role_for
from app.chat_cursors import cursor_codec, resync_required
from app.channels import ChannelRef, resolve_channel
from app.content_revisions import ContentChangeInput, change_content
from app.db.models import ChatMessage, GameEvent, MessageReaction, PrivateMessage, RoleRequest, Room, RoomDelivery, RoomMember, User
from app.db.session import get_db

router=APIRouter(prefix='/api/v2/rooms/{room_id}/channels/{channel_id}',tags=['chat timeline'])


class SourceRef(BaseModel):
    kind: Literal['chat','private_message','role_request']
    id: UUID


class Author(BaseModel):
    id: UUID
    name: str
    is_host: bool


class Actions(BaseModel):
    edit: bool
    delete: bool
    react: bool=False


class Revision(BaseModel):
    action: Literal['send','edit','delete']
    text: str | None
    created_at: datetime


class ReactionSummary(BaseModel):
    emoji: str
    count: int
    mine: bool


class TimelineItem(BaseModel):
    id: str
    source: SourceRef
    type: Literal['message','role_request']='message'
    author: Author
    text: str
    created_at: datetime
    edited_at: datetime | None
    deleted: bool
    phase_type: str | None
    round_number: int | None
    actions: Actions
    history: list[Revision] | None
    reactions: list[ReactionSummary] = Field(default_factory=list)
    request: dict | None = None
    read_receipt: UUID | None = None


class TimelinePage(BaseModel):
    items: list[TimelineItem]
    next_before: str | None
    next_changes: str
    next_after: str | None = None


class ChangeItem(BaseModel):
    item: TimelineItem
    insert: bool


class ChangesPage(BaseModel):
    changes: list[ChangeItem]
    next_changes: str
    has_more: bool


def utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def item_output(message: ChatMessageOutput, room: Room, actor_id: UUID) -> TimelineItem:
    editable=(actor_id==room.owner_user_id and room.status=='playing'
              and message.sender_user_id==actor_id and not message.deleted)
    return TimelineItem(id=f'{message.source_kind}:{message.id}',
        source=SourceRef(kind=message.source_kind,id=message.id),
        author=Author(id=message.sender_user_id,name=message.sender_name,is_host=message.sender_user_id==room.owner_user_id),
        text=message.text,created_at=utc(message.created_at),edited_at=utc(message.edited_at) if message.edited_at else None,
        deleted=message.deleted,phase_type=message.phase_type,round_number=message.round_number,
        actions=Actions(edit=editable,delete=editable),history=[Revision(action=entry['action'],text=entry['text'],
            created_at=utc(datetime.fromisoformat(entry['created_at']))) for entry in message.history] if message.history else None)


async def output_items(db, messages, room, user, ref):
    ids = [message.event_id for message in messages]
    rows = (await db.execute(select(MessageReaction.source_event_id, MessageReaction.emoji,
        func.count(), func.bool_or(MessageReaction.user_id == user.id)).where(
            MessageReaction.room_id == room.id, MessageReaction.source_event_id.in_(ids))
        .group_by(MessageReaction.source_event_id, MessageReaction.emoji)
        .order_by(MessageReaction.emoji))).all() if ids else []
    reactions = {}
    for event_id, emoji, count, mine in rows:
        reactions.setdefault(event_id, []).append(ReactionSummary(emoji=emoji, count=count, mine=mine))
    request_ids = [message.id for message in messages if message.source_kind == 'role_request']
    requests = {request.id:(request,receipt) for request,receipt in (await db.execute(
        select(RoleRequest,RoomDelivery.id).join(GameEvent,
            (GameEvent.room_id == RoleRequest.room_id) & (GameEvent.sequence == RoleRequest.updated_sequence))
        .outerjoin(RoomDelivery,(RoomDelivery.source_event_id == GameEvent.id) & (RoomDelivery.user_id == user.id)
            & (RoomDelivery.room_id == room.id))
        .where(RoleRequest.room_id == room.id,RoleRequest.id.in_(request_ids)))).all()} if request_ids else {}
    result = []
    for message in messages:
        item = item_output(message, room, user.id)
        item.actions.react = ref.capabilities.react and not item.deleted
        item.reactions = reactions.get(message.event_id, [])
        if message.source_kind == 'role_request':
            request,receipt=requests[message.id]
            pending=room.status=='playing' and request.status in {'pending','in_review'}
            item.type='role_request'
            item.actions=Actions(edit=False,delete=False,react=False)
            item.request={'id':str(request.id),'status':request.status,'role_name':request.role_name,
                'reply_text':request.reply_text,'can_withdraw':pending and request.user_id==user.id,
                'can_review':pending and room.owner_user_id==user.id}
            item.read_receipt=receipt
        result.append(item)
    return result


async def read_scope(db: AsyncSession, room_id: UUID, channel_id: UUID, user: User):
    ref=await resolve_channel(db,room_id,channel_id,user)
    if ref.kind not in {'common','team','moderator_dm'}:
        raise HTTPException(status_code=409,detail='Kênh này dùng công cụ riêng')
    room=await db.get(Room,room_id)
    host=room.owner_user_id==user.id
    member=None if host else await db.get(RoomMember,(room_id,user.id))
    epoch_data={'finished':room.status=='finished','joined':utc(member.joined_at).isoformat() if member else None,
                'consent':member.consent_version if member else None}
    if ref.kind=='team' and not host and room.status!='finished':
        role=await role_for(db,room_id,user.id)
        epoch_data['team']=role[0].get('team_chat_label') if role else None
    epoch=hashlib.sha256(json.dumps(epoch_data,sort_keys=True).encode()).digest()[:16]
    participant=ref.participant_user_id if host or room.status=='finished' else None
    kind='host' if ref.kind=='moderator_dm' else ref.kind
    async def read(*,before=None,after=None,updates=None,limit=30,tombstones=False,anchor=None):
        return await read_chat_page(db,user,room_id,kind,before,after,updates,limit,ref.team_label,participant,
                                    include_private=True,include_tombstones=tombstones,anchor_item=anchor,include_requests=True)
    return room,ref,epoch,read


def only_queries(request: Request, allowed: set[str]):
    if set(request.query_params)-allowed:
        raise HTTPException(status_code=422,detail='Tham số không được hỗ trợ')


@router.get('/timeline',response_model=TimelinePage)
async def timeline(room_id: UUID,channel_id: UUID,request: Request,before: str | None=None,
                   after: str | None=None, around_item: str | None=Query(default=None,max_length=64),
                   limit: int=Query(default=30,ge=1,le=50),user: User=Depends(current_user),db: AsyncSession=Depends(get_db)):
    only_queries(request,{'before','after','around_item','limit'})
    if sum(value is not None for value in (before,after,around_item)) > 1:
        raise HTTPException(status_code=422,detail='Chỉ dùng một hướng phân trang')
    anchor=None
    if around_item is not None:
        try:
            kind,source_id=around_item.split(':')
            if kind not in {'chat','private_message','role_request'}: raise ValueError()
            anchor=(kind,UUID(source_id))
        except ValueError:
            raise HTTPException(status_code=422,detail='Mốc đọc không hợp lệ') from None
    room,ref,epoch,read=await read_scope(db,room_id,channel_id,user)
    codec=cursor_codec()
    token=codec.decode(before if before is not None else after,room_id,user.id,channel_id,
        'before' if before is not None else 'after',epoch) if before is not None or after is not None else None
    page=await read(before=token.before if before is not None else None,
        after=token.before if after is not None else None,anchor=anchor,limit=limit)
    if token and page.room_cursor < max(token.created,token.updated,token.before):
        raise resync_required()
    created=token.created if token else (page.room_cursor if anchor else page.messages[-1].sequence if page.messages else 0)
    updated=token.updated if token else page.room_cursor
    next_before=codec.encode(room_id,user.id,channel_id,'before',epoch,created=created,updated=updated,
        before=page.next_before_sequence) if page.next_before_sequence is not None else None
    return TimelinePage(items=await output_items(db,page.messages,room,user,ref),next_before=next_before,
        next_changes=codec.encode(room_id,user.id,channel_id,'changes',epoch,created=created,updated=updated),
        next_after=codec.encode(room_id,user.id,channel_id,'after',epoch,created=created,updated=updated,
            before=page.next_after_sequence) if page.next_after_sequence is not None else None)


@router.get('/changes',response_model=ChangesPage)
async def changes(room_id: UUID,channel_id: UUID,request: Request,cursor: str | None=None,
                  limit: int=Query(default=30,ge=1,le=50),user: User=Depends(current_user),db: AsyncSession=Depends(get_db)):
    only_queries(request,{'cursor','limit'})
    room,ref,epoch,read=await read_scope(db,room_id,channel_id,user)
    if cursor is None:
        raise resync_required()
    codec=cursor_codec()
    token=codec.decode(cursor,room_id,user.id,channel_id,'changes',epoch)
    # Scan creations separately from latest revisions. A new source edited before a changes
    # page is fetched must still enter the timeline; update-only pages never advance this boundary.
    arrivals=await read(after=token.created,limit=limit,tombstones=True)
    if arrivals.room_cursor < max(token.updated,token.created):
        raise resync_required()
    if arrivals.messages:
        created=arrivals.messages[-1].sequence
        return ChangesPage(changes=[ChangeItem(item=item,insert=True)
            for item in await output_items(db,arrivals.messages,room,user,ref)],has_more=True,next_changes=codec.encode(
                room_id,user.id,channel_id,'changes',epoch,created=created,updated=token.updated))
    page=await read(updates=token.updated,limit=limit)
    if page.room_cursor < max(token.updated,token.created):
        raise resync_required()
    updated=page.next_after_updated_sequence if page.next_after_updated_sequence is not None else page.room_cursor
    return ChangesPage(changes=[ChangeItem(item=item,insert=False)
        for item in await output_items(db,page.messages,room,user,ref)],has_more=page.next_after_updated_sequence is not None,
        next_changes=codec.encode(room_id,user.id,channel_id,'changes',epoch,created=token.created,updated=updated,reuse=cursor))


@router.post('/items/{source_kind}/{source_id}/revisions',response_model=SourceRef)
async def revise_item(room_id: UUID,channel_id: UUID,source_kind: Literal['chat','private_message'],source_id: UUID,
                      body: ContentChangeInput,user: User=Depends(current_user),db: AsyncSession=Depends(get_db)):
    await require_room_host(db,room_id,user.id)
    _locked_room=await db.scalar(select(Room).where(Room.id==room_id).with_for_update()
                                 .execution_options(populate_existing=True))
    ref=await resolve_channel(db,room_id,channel_id,user)
    model=ChatMessage if source_kind=='chat' else PrivateMessage
    source=await db.scalar(select(model).where(model.room_id==room_id,model.id==source_id))
    valid=False
    if source is not None and source_kind=='chat':
        expected='host' if ref.kind=='moderator_dm' else ref.kind
        valid=(source.channel==expected and source.team_label==ref.team_label
               and source.participant_user_id==ref.participant_user_id)
    elif source is not None:
        valid=ref.kind=='moderator_dm' and source.user_id==ref.participant_user_id
    if not valid:
        raise HTTPException(status_code=404,detail='Không tìm thấy tin trong kênh')
    await change_content(room_id,source.event_id,body,user,db)
    return SourceRef(kind=source_kind,id=source_id)
