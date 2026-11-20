"""No public ordinals; scoped AEAD boundaries, immutable creation scans and revisions."""
import asyncio
from uuid import UUID

from app import chat
from test_chat_timeline import match, post, command, ORIGIN
from test_channels import catalog, find, v2, addressed


def assert_no_ordinals(value):
    if isinstance(value,dict):
        assert not {'sequence','updated_sequence','event_id','source_event_id','room_cursor','recipient_ids'} & value.keys(),value
        for child in value.values():assert_no_ordinals(child)
    elif isinstance(value,list):
        for child in value:assert_no_ordinals(child)


async def fetch(client,url,**params):
    response=await client.get(url,params=params)
    assert response.status_code==200,(response.status_code,response.text[:300])
    data=response.json();assert_no_ordinals(data);return data


async def drain(client,url,cursor,*,limit=3):
    inserted=[];changes=[]
    for _ in range(100):
        page=await fetch(client,url,cursor=cursor,limit=limit)
        inserted.extend(item['item'] for item in page['changes'] if item['insert'])
        changes.extend(page['changes'])
        cursor=page['next_changes']
        if not page['has_more']:return inserted,changes,cursor
    raise AssertionError('changes did not converge')


def test_v2_pagination_scope_and_unrelated_activity(monkeypatch):
    monkeypatch.setattr(chat,'CHAT_BURST_LIMIT',100)
    monkeypatch.setattr(chat,'CHAT_MINUTE_LIMIT',100)
    async def run():
        async with match() as (clients,ids,base,room_id):
            host,a,b,_,outside=clients
            own=find(await catalog(a,base),'moderator_dm')
            host_peer=find(await catalog(host,base),'moderator_dm',participant_user_id=ids[1])
            own_base=v2(base)+'/'+own['id']
            general=find(await catalog(a,base),'common')
            common_base=v2(base)+'/'+general['id']
            empty=await fetch(a,common_base+'/timeline')
            stable=await fetch(a,common_base+'/changes',cursor=empty['next_changes'])
            assert stable=={'changes':[],'next_changes':empty['next_changes'],'has_more':False}
            # A valid token from a newer restored state must resync, never wait past future arrivals.
            from app.chat_timeline import read_scope
            from app.chat_cursors import cursor_codec
            from app.db.session import session_factory
            async with session_factory()() as db:
                from app.db.models import User
                user=await db.get(User,UUID(ids[1]))
                _,_,epoch,_=await read_scope(db,room_id,UUID(general['id']),user)
                ahead=cursor_codec().encode(room_id,user.id,UUID(general['id']),'changes',epoch,created=100,updated=50)
            response=await a.get(common_base+'/changes',params={'cursor':ahead})
            assert response.status_code==409

            await post(host,base+f'/game/players/{ids[2]}/private-messages',command(text='B private activity'))
            await post(b,base+'/chat/messages',command(channel='host',text='B private reply'))
            await post(host,base+f'/game/players/{ids[2]}/actions',command(action='note',text='Private note for B'))
            assert await fetch(a,common_base+'/changes',cursor=empty['next_changes'])==stable
            expected=[]
            for index in range(33):
                if index%2:
                    ack=await addressed(host,base,host_peer,command(text=f'Chat {index}'))
                    assert ack.status_code==200
                    expected.append(('chat',ack.json()['id']))
                else:
                    await post(host,base+f'/game/players/{ids[1]}/private-messages',command(text=f'Private {index}'))
                    item=(await fetch(a,own_base+'/timeline',limit=1))['items'][0]
                    expected.append((item['source']['kind'],item['source']['id']))
            all_items=[];before=None
            while True:
                page=await fetch(a,own_base+'/timeline',limit=5,**({'before':before} if before else {}))
                all_items=page['items']+all_items
                before=page['next_before']
                if before is None:break
                assert len(before)==114
            assert [(item['source']['kind'],item['source']['id']) for item in all_items]==expected
            page=await fetch(a,own_base+'/timeline',limit=5)
            cursor=page['next_changes']
            for client,url,token in [(b,own_base+'/changes',cursor),
                (a,common_base+'/changes',cursor),(a,own_base+'/timeline',cursor)]:
                params={'before':token} if url.endswith('timeline') else {'cursor':token}
                response=await client.get(url,params=params)
                assert response.status_code in (404,409)
                assert token not in response.text
            for token in ('not-a-cursor','x'*10000,cursor[:25]+'!'+cursor[26:]):
                response=await a.get(own_base+'/changes',params={'cursor':token})
                assert response.status_code==409 and response.json()['detail']['code']=='chat_resync_required'
                assert token not in response.text
            response=await a.get(own_base+'/timeline',params={'after_sequence':0})
            assert response.status_code==422
            assert (await outside.get(own_base+'/timeline')).status_code==403
            assert (await a.get(own_base+'/changes',params={'cursor':cursor,'limit':51})).status_code==422
    asyncio.run(run())


def test_v2_edits_unseen_creations_tombstones_and_postgame(monkeypatch):
    monkeypatch.setattr(chat,'CHAT_BURST_LIMIT',100)
    monkeypatch.setattr(chat,'CHAT_MINUTE_LIMIT',100)
    async def run():
        async with match() as (clients,ids,base,room_id):
            host,a,b,*_=clients
            peer=find(await catalog(host,base),'moderator_dm',participant_user_id=ids[1])
            url=v2(base)+'/'+peer['id']
            initial=await fetch(a,url+'/timeline')
            created=[]
            for index in range(8):
                response=await addressed(host,base,peer,command(text=f'Arrival {index}'))
                assert response.status_code==200
                created.append(response.json()['id'])
            # Edit a source before it has ever reached this client.
            edit_url=url+'/items/chat/'+created[-1]+'/revisions'
            response=await host.post(edit_url,json=command(action='edit',text='Edited before first delivery'),headers={'Origin':ORIGIN})
            assert response.status_code==200
            assert_no_ordinals(response.json())
            response=await a.post(edit_url,json=command(action='delete'),headers={'Origin':ORIGIN})
            assert response.status_code==403
            first=await fetch(a,url+'/changes',cursor=initial['next_changes'],limit=2)
            assert first['has_more']
            # An update after the first page must not make this still-unseen source disappear.
            await host.post(url+'/items/chat/'+created[4]+'/revisions',json=command(action='edit',text='Changed during pagination'),headers={'Origin':ORIGIN})
            await host.post(url+'/items/chat/'+created[5]+'/revisions',json=command(action='delete'),headers={'Origin':ORIGIN})
            inserted,changes,cursor=await drain(a,url+'/changes',first['next_changes'],limit=2)
            inserted=[entry['item'] for entry in first['changes'] if entry['insert']]+inserted
            assert [item['source']['id'] for item in inserted]==created
            assert inserted[4]['text']=='Changed during pagination'
            assert inserted[5]['deleted'] and inserted[5]['text']==''
            assert inserted[-1]['text']=='Edited before first delivery'
            assert await fetch(a,url+'/changes',cursor=cursor)=={'changes':[],'next_changes':cursor,'has_more':False}
            response=await host.post(url+'/items/chat/'+created[0]+'/revisions',json=command(action='edit',text='Old source edit'),headers={'Origin':ORIGIN})
            assert response.status_code==200
            inserted_again,changes,cursor=await drain(a,url+'/changes',cursor)
            assert inserted_again==[]
            assert any(change['item']['text']=='Old source edit' and not change['insert'] for change in changes)
            # A source cannot be addressed through another DM, even by the host.
            other=find(await catalog(host,base),'moderator_dm',participant_user_id=ids[2])
            response=await host.post(v2(base)+'/'+other['id']+'/items/chat/'+created[0]+'/revisions',json=command(action='delete'),headers={'Origin':ORIGIN})
            assert response.status_code==404
            await post(host,base+'/game/finish',{'result_label':'Done','result_reason':'Opaque transcript'})
            response=await a.get(url+'/changes',params={'cursor':cursor})
            assert response.status_code==409 and response.json()['detail']['code']=='chat_resync_required'
            finished=await fetch(b,url+'/timeline')
            assert len(finished['items'])==8
            assert all(not item['actions']['edit'] for item in finished['items'])
            assert finished['items'][5]['deleted']
            assert [entry['action'] for entry in finished['items'][5]['history']]==['send','delete']
    asyncio.run(run())


def test_v2_team_read_epoch_changes_only_for_affected_actor():
    async def run():
        async with match() as (clients,ids,base,room_id):
            host,*players,_=clients
            assignments=(await host.get(base+'/roles/assignments')).json()
            wolves=[item['user_id'] for item in assignments if item['role']['team_chat_label']]
            by_id=dict(zip(ids[1:4],players))
            actor,other=by_id[wolves[0]],by_id[wolves[1]]
            team=find(await catalog(actor,base),'team')
            url=v2(base)+'/'+team['id']
            await post(host,base+'/game/advance',{'expected_phase_type':'day','expected_round_number':0})
            response=await addressed(actor,base,team,command(text='Own team history'))
            assert response.status_code==200
            snapshot=await fetch(actor,url+'/timeline')
            other_snapshot=await fetch(other,url+'/timeline')
            await post(host,base+f'/game/players/{wolves[0]}/actions',command(action='change_team',text='Another team'))
            response=await actor.get(url+'/changes',params={'cursor':snapshot['next_changes']})
            assert response.status_code==409
            assert (await fetch(other,url+'/changes',cursor=other_snapshot['next_changes']))['next_changes']==other_snapshot['next_changes']
            history=await fetch(actor,url+'/timeline')
            assert len(history['items'])==1
            await post(host,base+f'/players/{wolves[0]}/kick',command(reason='Revoke current access'))
            assert (await actor.get(url+'/changes',params={'cursor':history['next_changes']})).status_code==403
    asyncio.run(run())


def test_v2_revision_waiting_for_finish_cannot_write_after_finish(monkeypatch):
    from app import chat_timeline
    from app.auth import now_utc
    from app.db.events import append_event
    from app.db.models import Room,ChatMessage
    from app.db.session import session_factory
    from sqlalchemy import select
    async def run():
        async with match() as (clients,ids,base,room_id):
            host=clients[0]
            peer=find(await catalog(host,base),'moderator_dm',participant_user_id=ids[1])
            sent=await addressed(host,base,peer,command(text='Original before finish'))
            source_id=sent.json()['id']
            entered=asyncio.Event()
            original=chat_timeline.require_room_host
            async def signal(*args,**kwargs):
                await original(*args,**kwargs);entered.set()
            monkeypatch.setattr(chat_timeline,'require_room_host',signal)
            async with session_factory()() as db:
                room=await db.scalar(select(Room).where(Room.id==room_id).with_for_update())
                pending=asyncio.create_task(host.post(v2(base)+'/'+peer['id']+'/items/chat/'+source_id+'/revisions',
                    json=command(action='edit',text='Must not commit'),headers={'Origin':ORIGIN}))
                await asyncio.wait_for(entered.wait(),timeout=2)
                room.status='finished';room.ended_at=now_utc();room.result_label='Done'
                await append_event(db,room_id,UUID(ids[0]),'game.finished','room',{})
                await db.commit()
            response=await asyncio.wait_for(pending,timeout=3)
            assert response.status_code==409,response.text
            item=(await fetch(host,v2(base)+'/'+peer['id']+'/timeline'))['items'][0]
            assert item['text']=='Original before finish' and len(item['history'])==1
    asyncio.run(run())
