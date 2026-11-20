"""Restore a bounded history window without walking history or exposing source positions."""
import asyncio
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app import chat
from app.db.models import ChatMessage, PrivateMessage
from app.db.session import session_factory
from test_chat_timeline import match, post, command, ORIGIN
from test_channels import catalog, find, v2, addressed
from test_chat_timeline_v2 import fetch, drain


def test_anchor_forward_backward_pages_and_live_boundaries(monkeypatch):
    monkeypatch.setattr(chat,'CHAT_BURST_LIMIT',100)
    monkeypatch.setattr(chat,'CHAT_MINUTE_LIMIT',100)
    async def run():
        async with match() as (clients,ids,base,room_id):
            host,a,b,*_=clients
            channel=find(await catalog(a,base),'common');url=v2(base)+'/'+channel['id']
            sources=[]
            for index in range(33):
                sent=await addressed(host,base,channel,command(text=f'History {index}'))
                assert sent.status_code==200
                sources.append('chat:'+sent.json()['id'])
            page=await fetch(a,url+'/timeline',around_item=sources[7],limit=5)
            assert [item['id'] for item in page['items']]==sources[7:12]
            assert len(page['next_before'])==len(page['next_after'])==114
            old=await fetch(a,url+'/timeline',before=page['next_before'],limit=5)
            assert [item['id'] for item in old['items']]==sources[2:7]
            stable=await fetch(a,url+'/changes',cursor=page['next_changes'])
            assert stable=={'changes':[],'next_changes':page['next_changes'],'has_more':False}
            later=[];token=page['next_after']
            while token:
                forward=await fetch(a,url+'/timeline',after=token,limit=5)
                later.extend(item['id'] for item in forward['items']);token=forward['next_after']
            assert later==sources[12:]
            for params in ({'before':page['next_after']},{'after':page['next_before']},
                           {'before':page['next_changes']},{'after':page['next_changes']}):
                assert (await a.get(url+'/timeline',params=params)).status_code==409
            for params in ({'around_item':sources[7],'before':page['next_before']},
                           {'around_item':'chat:not-uuid'},{'around_item':'other:'+str(uuid4())}):
                assert (await a.get(url+'/timeline',params=params)).status_code==422
            await addressed(host,base,channel,command(text='Live after restored snapshot'))
            await host.post(url+'/items/'+sources[8].replace(':','/')+'/revisions',
                json=command(action='edit',text='Updated in window'),headers={'Origin':ORIGIN})
            inserts,changes,cursor=await drain(a,url+'/changes',page['next_changes'])
            assert [item['text'] for item in inserts]==['Live after restored snapshot']
            assert any(change['item']['text']=='Updated in window' for change in changes)
            await post(host,base+'/game/finish',{'result_label':'Done','result_reason':'Anchor history'})
            assert (await a.get(url+'/timeline',params={'after':page['next_after']})).status_code==409
            finished=await fetch(b,url+'/timeline',around_item=sources[7],limit=5)
            assert finished['items'][1]['text']=='Updated in window'
    asyncio.run(run())


def test_anchor_source_acl_deleted_source_and_same_uuid():
    async def run():
        async with match() as (clients,ids,base,room_id):
            host,a,b,_,outside=clients
            own=find(await catalog(a,base),'moderator_dm')
            peer=find(await catalog(host,base),'moderator_dm',participant_user_id=ids[1])
            other=find(await catalog(host,base),'moderator_dm',participant_user_id=ids[2])
            url=v2(base)+'/'+own['id'];other_url=v2(base)+'/'+other['id']
            await post(host,base+f'/game/players/{ids[1]}/private-messages',command(text='Private anchor'))
            sent=await addressed(host,base,peer,command(text='Chat anchor'))
            async with session_factory()() as db:
                private=await db.scalar(select(PrivateMessage).where(PrivateMessage.room_id==room_id))
                key=str(private.id)
                await db.execute(update(ChatMessage).where(ChatMessage.id==UUID(sent.json()['id'])).values(id=private.id))
                await db.commit()
            for kind,text in [('chat','Chat anchor'),('private_message','Private anchor')]:
                page=await fetch(a,url+'/timeline',around_item=kind+':'+key,limit=1)
                assert page['items'][0]['text']==text
                assert (await b.get(url+'/timeline',params={'around_item':kind+':'+key})).status_code==404
                assert (await host.get(other_url+'/timeline',params={'around_item':kind+':'+key})).status_code==404
                assert (await outside.get(url+'/timeline',params={'around_item':kind+':'+key})).status_code==403
            await host.post(url+'/items/chat/'+key+'/revisions',json=command(action='delete'),headers={'Origin':ORIGIN})
            missing=await a.get(url+'/timeline',params={'around_item':'chat:'+str(uuid4())})
            deleted=await a.get(url+'/timeline',params={'around_item':'chat:'+key})
            assert missing.status_code==deleted.status_code==404 and missing.json()==deleted.json()
            await post(host,base+'/game/finish',{'result_label':'Done','result_reason':'Anchor deleted transcript'})
            page=await fetch(b,url+'/timeline',around_item='chat:'+key,limit=1)
            assert page['items'][0]['deleted'] and page['items'][0]['history']
    asyncio.run(run())


def test_anchor_team_history_requires_original_audience():
    async def run():
        async with match() as (clients,ids,base,room_id):
            host,*players,_=clients
            assignments=(await host.get(base+'/roles/assignments')).json()
            wolves=[row['user_id'] for row in assignments if row['role']['team_chat_label']]
            villager=next(row['user_id'] for row in assignments if not row['role']['team_chat_label'])
            by_id=dict(zip(ids[1:4],players));actor=by_id[wolves[0]]
            team=find(await catalog(actor,base),'team');url=v2(base)+'/'+team['id']
            await post(host,base+'/game/advance',{'expected_phase_type':'day','expected_round_number':0})
            sent=await addressed(actor,base,team,command(text='Only original team'))
            source='chat:'+sent.json()['id']
            await post(host,base+f'/game/players/{wolves[0]}/actions',command(action='change_team',text='Other team'))
            assert (await fetch(actor,url+'/timeline',around_item=source))['items'][0]['id']==source
            await post(host,base+f'/game/players/{villager}/actions',command(action='change_team',text='Wolf team'))
            response=await by_id[villager].get(url+'/timeline',params={'around_item':source})
            assert response.status_code==404
            await post(host,base+f'/players/{wolves[0]}/kick',command(reason='Revoke history'))
            assert (await actor.get(url+'/timeline',params={'around_item':source})).status_code==403
    asyncio.run(run())
