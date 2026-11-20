"""Public v2 request source, revision receipts, canonical IDs, ACL and protocol retirement."""
import asyncio
from uuid import UUID, uuid4
import pytest
from sqlalchemy import func, select, update
from app import chat_reads
from app.db.models import ChatMessage, PrivateMessage, RoleRequest
from app.db.session import session_factory
from test_chat_timeline import match, post, command, ORIGIN
from test_channels import catalog, find, v2, addressed
from test_chat_timeline_v2 import fetch, drain, assert_no_ordinals
from test_room_delivery import sync
from test_live import ASGISocket

async def submit(client,base,text='Đoán số lượng, không giới hạn mục tiêu'):
    return await post(client,base+'/role-requests',command(text=text))
async def review(host,base,key,**kw):
    return await post(host,base+'/role-requests/'+key+'/review',command(**kw))
async def ack(client,url,item):
    return await client.post(url+'/read',json={'visible_item_id':item['id'],'read_receipt':item['read_receipt']},headers={'Origin':ORIGIN})


def test_request_source_shared_with_queue_and_private_live_delivery():
    async def run():
        async with match(active=True) as (clients,ids,base,room_id):
            host,a,b,*_=clients
            dm=find(await catalog(a,base),'moderator_dm');url=v2(base)+'/'+dm['id']
            before=(await sync(b,base))['next_cursor'];b_catalog=await catalog(b,base)
            await post(host,base+f'/game/players/{ids[1]}/private-messages',command(text='GM one-way'))
            saved=await submit(a,base)
            sent=await addressed(a,base,dm,command(text='Chat thường không trở thành yêu cầu'));assert sent.status_code==200
            page=await fetch(a,url+'/timeline');assert [item['source']['kind'] for item in page['items']]==['private_message','role_request','chat']
            item=page['items'][1];assert item['source']['id']==saved['id'] and item['request']['can_withdraw']
            assert not item['actions']['react'] and item['request']['reply_text'] is None
            queue=(await host.get(base+'/role-requests')).json();assert queue['requests'][0]['id']==saved['id']
            assert queue['requests'][0]['created_at']==queue['requests'][0]['updated_at']
            assert (await host.get(base+'/role-requests/'+saved['id'])).json()['id']==saved['id']
            assert (await b.get(url+'/timeline')).status_code==404
            assert await catalog(b,base)==b_catalog
            assert (await sync(b,base,cursor=before))['next_cursor']==before
            await review(host,base,saved['id'],status='accepted',reply_text='Kết quả là 2, đã xác nhận.')
            inserts,changes,_=await drain(a,url+'/changes',page['next_changes'])
            assert not inserts
            changed=next(change['item'] for change in changes if change['item']['id']==item['id'])
            assert changed['request']['status']=='accepted' and changed['request']['reply_text']=='Kết quả là 2, đã xác nhận.'
            assert changed['read_receipt']!=item['read_receipt']
            assert not {'linked_event_ids','role_id'} & changed['request'].keys()
            async with session_factory()() as db:
                assert await db.scalar(select(func.count()).select_from(RoleRequest).where(RoleRequest.room_id==room_id))==1
                assert await db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.room_id==room_id))==1
    asyncio.run(run())


def test_request_read_receipt_does_not_ack_a_newer_review(monkeypatch):
    async def run():
        async with match(active=True) as (clients,ids,base,room_id):
            host,a,b,*_=clients;dm=find(await catalog(a,base),'moderator_dm');url=v2(base)+'/'+dm['id']
            saved=await submit(a,base)
            host_item=(await fetch(host,url+'/timeline'))['items'][0]
            own=(await fetch(a,url+'/timeline'))['items'][0]
            assert find(await catalog(host,base),'moderator_dm',participant_user_id=ids[1])['unread']
            assert (await ack(host,url,host_item)).status_code==204
            await review(host,base,saved['id'],status='in_review',reply_text='Đang xem')
            current=(await fetch(a,url+'/timeline'))['items'][0]
            assert find(await catalog(a,base),'moderator_dm')['unread']
            assert (await ack(a,url,host_item)).status_code==404
            assert (await ack(a,url,own)).status_code==204
            assert find(await catalog(a,base),'moderator_dm')['unread']
            entered=asyncio.Event();release=asyncio.Event();original=chat_reads.advance_read
            async def delayed(*args):entered.set();await release.wait();return await original(*args)
            monkeypatch.setattr(chat_reads,'advance_read',delayed)
            pending=asyncio.create_task(ack(a,url,current));await asyncio.wait_for(entered.wait(),2)
            await review(host,base,saved['id'],status='accepted',reply_text='Phản hồi tới trong khi ACK đang chờ')
            release.set();assert (await asyncio.wait_for(pending,3)).status_code==204
            assert find(await catalog(a,base),'moderator_dm')['unread']
            latest=(await fetch(a,url+'/timeline'))['items'][0]
            assert (await ack(a,url,latest)).status_code==204
            assert not find(await catalog(a,base),'moderator_dm')['unread']
            other=await submit(a,base,'Yêu cầu thứ hai')
            other_item=(await fetch(a,url+'/timeline'))['items'][-1]
            assert (await ack(a,url,{**latest,'read_receipt':other_item['read_receipt']})).status_code==404
            assert (await a.post(url+'/read',json={'visible_item_id':latest['id']},headers={'Origin':ORIGIN})).status_code==409
    asyncio.run(run())


def test_request_source_collision_seek_withdraw_and_postgame():
    async def run():
        async with match(active=True) as (clients,ids,base,room_id):
            host,a,b,*_=clients;dm=find(await catalog(a,base),'moderator_dm');url=v2(base)+'/'+dm['id']
            saved=await submit(a,base)
            await post(host,base+f'/game/players/{ids[1]}/private-messages',command(text='Different source'))
            async with session_factory()() as db:
                await db.execute(update(PrivateMessage).where(PrivateMessage.room_id==room_id).values(id=UUID(saved['id'])))
                await db.commit()
            page=await fetch(a,url+'/timeline');assert len({item['id'] for item in page['items']})==2
            seek=await fetch(a,url+'/timeline',around_item='role_request:'+saved['id'],limit=1)
            assert seek['items'][0]['request']['status']=='pending'
            assert (await a.put(url+'/items/role_request/'+saved['id']+'/reactions',json=command(emoji='🐺'),headers={'Origin':ORIGIN})).status_code==404
            assert (await b.post(base+'/role-requests/'+saved['id']+'/withdraw',json=command(),headers={'Origin':ORIGIN})).status_code==404
            await post(a,base+'/role-requests/'+saved['id']+'/withdraw',command())
            item=(await fetch(a,url+'/timeline'))['items'][0];assert item['request']['status']=='withdrawn' and not item['request']['can_withdraw']
            await post(host,base+'/game/finish',{'result_label':'Done','result_reason':'Request transcript'})
            expanded=await fetch(b,url+'/timeline');assert expanded['items'][0]['request']['status']=='withdrawn'
            assert not expanded['items'][0]['request']['can_review']
            assert (await ack(b,url,expanded['items'][0])).status_code==204
            transcript=(await b.get(base+'/transcript',params={'category':'requests'}))
            # Transcript category names may evolve; the canonical request remains in full journal.
            transcript=await b.get(base+'/transcript',params={'limit':100})
            assert transcript.status_code==200 and any(item['kind']=='role.request_withdrawn' for item in transcript.json()['events'])
    asyncio.run(run())


@pytest.mark.parametrize('path',['/chat/messages?channel=common','/chat/timeline?channel=host','/game/private-messages','/role-requests'])
def test_numeric_reads_are_not_a_player_bypass(path):
    async def run():
        async with match(active=True) as (clients,ids,base,room_id):
            host,a,b,_,outside=clients
            response=await a.get(base+path);assert response.status_code==426
            assert response.json()['detail']['code']=='client_upgrade_required'
            assert (await outside.get(base+path)).status_code==403
            assert (await a.get(base+'/chat/context')).json()['activity']==[]
            channel=find(await catalog(a,base),'common');assert_no_ordinals((await a.get(v2(base)+'/'+channel['id']+'/timeline')).json())
    asyncio.run(run())


def test_legacy_socket_closes_without_numeric_ready_frame():
    async def run():
        async with match() as (clients,ids,base,room_id):
            for client in clients[:2]:
                socket=ASGISocket(base+'/live',client.cookies.get('ma_soi_session'),after_sequence=0)
                assert (await socket.connect())['type']=='websocket.accept'
                assert await asyncio.wait_for(socket.output.get(),1)=={'type':'websocket.close','code':4406,'reason':''}
                assert socket.output.empty();await socket.close()
    asyncio.run(run())


def test_public_notice_metadata_does_not_reveal_private_activity():
    async def run():
        async with match(active=True) as (clients,ids,base,room_id):
            host,a,b,*_=clients
            event=await post(host,base+'/game/events/announcements',command(text='Nội dung công khai'))
            await submit(b,base,'Hoạt động kín B')
            await post(host,base+f'/game/players/{ids[2]}/actions',command(action='note',text='Ghi chú kín'))
            await post(host,base+'/game/events/corrections',command(source_event_id=event['event_id'],reason='Điều chỉnh',corrected_text='Đính chính công khai'))
            for path in ['/game/events/announcements','/game/events/public-corrections']:
                response=await a.get(base+path);assert response.status_code==200
                for item in response.json():assert 'sequence' not in item and 'source_sequence' not in item
                host_response=(await host.get(base+path)).json();assert all(isinstance(item['sequence'],int) for item in host_response)
    asyncio.run(run())

@pytest.mark.parametrize('change',['finish','kick'])
def test_queued_role_request_rechecks_room_and_membership(monkeypatch,change):
    from app import role_requests
    from app.auth import now_utc
    from app.db.models import Room,RoomMember
    from app.db.events import append_event
    async def run():
        async with match(active=True) as (clients,ids,base,room_id):
            host,a,*_=clients;entered=asyncio.Event();original=role_requests.require_room_access
            async def signal(*args,**kw):
                result=await original(*args,**kw);entered.set();return result
            monkeypatch.setattr(role_requests,'require_room_access',signal)
            async with session_factory()() as db:
                room=await db.scalar(select(Room).where(Room.id==room_id).with_for_update())
                pending=asyncio.create_task(a.post(base+'/role-requests',json=command(text='Chờ khóa phòng'),headers={'Origin':ORIGIN}))
                await asyncio.wait_for(entered.wait(),2)
                if change=='finish':room.status='finished'
                else:
                    member=await db.get(RoomMember,(room_id,UUID(ids[1])));member.left_at=now_utc();member.seat_number=None
                await append_event(db,room_id,UUID(ids[0]),'game.finished' if change=='finish' else 'member.kicked','room',{})
                await db.commit()
            assert (await asyncio.wait_for(pending,3)).status_code==403
            async with session_factory()() as db:
                assert await db.scalar(select(func.count()).select_from(RoleRequest).where(RoleRequest.room_id==room_id))==0
    asyncio.run(run())
