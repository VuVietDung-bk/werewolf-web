"""A paginated, authorized timeline across both persisted chat message sources."""

from legacy_read_services import service_get
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update

from app import chat
from app.db.models import ChatMessage, GameEvent, PrivateMessage, Room, User
from app.db.session import session_factory
from app.main import app

ORIGIN = 'http://localhost:5173'


async def post(client, path, body=None):
    response = await client.post(path, json=body, headers={'Origin': ORIGIN})
    assert response.status_code in (200, 201), (path, response.status_code, response.text[:400])
    return response.json()


def command(**kw):
    return {'client_request_id': str(uuid4()), **kw}


@asynccontextmanager
async def match(*,active=False):
    ids = []
    room_id = None
    async with AsyncExitStack() as stack:
        clients = [await stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test')) for _ in range(5)]
        host, a, b, c, outside = clients
        try:
            for client, name in zip(clients, ['Host timeline', 'A timeline', 'B timeline', 'C timeline', 'Outside timeline']):
                ids.append((await post(client, '/api/auth/guest', {'display_name': name}))['id'])
            room = await post(host, '/api/rooms', {'name': 'Timeline fixture', 'seat_limit': 3})
            room_id = room['id']
            base = '/api/rooms/' + room_id
            for client in (a, b, c):
                await post(client, '/api/rooms/join', {'code': room['code'], 'accept_postgame_transcript': True})
            for name, qty, team in [('Wolf', 2, 'Wolf team'), ('Village', 1, None)]:
                await post(host, base + '/roles', {'name': name, 'description': name, 'faction_label': name,
                    'seer_result': name, 'team_chat_label': team, 'role_kind': 'active' if active else 'passive', 'quantity': qty,
                    'request_phases':['day','night'] if active else [],
                    'host_contact': {'reply_allowed': True}})
            response = await host.patch(base + '/settings', json={
                'name': 'Timeline fixture', 'seat_limit': 3, 'registration_open': False}, headers={'Origin': ORIGIN})
            assert response.status_code == 200
            await post(host, base + '/roles/distribute-random')
            await post(host, base + '/game/start')
            yield clients, ids, base, UUID(room_id)
        finally:
            async with session_factory()() as db:
                if room_id:
                    room = await db.get(Room, UUID(room_id))
                    if room:
                        await db.delete(room)
                        await db.flush()
                for user_id in ids:
                    await db.delete(await db.get(User, UUID(user_id)))
                await db.commit()


def test_mixed_dm_pagination_changes_and_postgame(monkeypatch):
    monkeypatch.setattr(chat, 'CHAT_BURST_LIMIT', 100)
    monkeypatch.setattr(chat, 'CHAT_MINUTE_LIMIT', 100)

    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, _, outside = clients
            url = base + '/chat/timeline'
            original = base + '/chat/messages'
            private = base + f'/game/players/{ids[1]}/private-messages'
            # Passive roles cannot initiate. A one-way host message opens replies without bypassing role settings.
            response = await a.post(original, json=command(channel='host', text='Cannot initiate'), headers={'Origin': ORIGIN})
            assert response.status_code == 403
            sources = []
            for index in range(37):
                if index % 2 == 0:
                    result = await post(host, private, command(text=f'Private {index}'))
                else:
                    result = await post(host, original, command(channel='host', participant_user_id=ids[1], text=f'Chat {index}'))
                sources.append(result['event_id'])
            await post(a, original, command(channel='host', text='Player reply'))
            async with session_factory()() as db:
                pm = await db.scalar(select(PrivateMessage).where(PrivateMessage.event_id == UUID(sources[0])))
                # Equal source UUIDs across tables and equal timestamps must not dedup or reorder sources.
                await db.execute(update(ChatMessage).where(ChatMessage.event_id == UUID(sources[1])).values(id=pm.id))
                timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
                await db.execute(update(ChatMessage).where(ChatMessage.room_id == room_id).values(created_at=timestamp))
                await db.execute(update(PrivateMessage).where(PrivateMessage.room_id == room_id).values(created_at=timestamp))
                await db.commit()
            expected = sources + [(await service_get(a,original, params={'channel': 'host'})).json()['messages'][-1]['event_id']]
            for client, extra in ((a, {}), (host, {'participant_user_id': ids[1]})):
                messages = []
                before = None
                while True:
                    params = {'channel': 'host', 'limit': 7, **extra}
                    if before is not None:
                        params['before_sequence'] = before
                    response = await service_get(client,url, params=params)
                    assert response.status_code == 200, response.text
                    data = response.json()
                    messages = data['messages'] + messages
                    before = data['next_before_sequence']
                    if before is None:
                        break
                assert [item['event_id'] for item in messages] == expected
                assert len({(item['source_kind'], item['id']) for item in messages}) == 38
                assert messages[0]['id'] == messages[1]['id']
                assert {item['source_kind'] for item in messages} == {'chat', 'private_message'}
            forward, after = [], 0
            while True:
                data = (await service_get(a,url, params={'channel': 'host', 'after_sequence': after, 'limit': 5})).json()
                forward.extend(item['event_id'] for item in data['messages'])
                after = data['next_after_sequence']
                if after is None:
                    break
            assert forward == expected
            assert len((await service_get(a,original, params={'channel': 'host'})).json()['messages']) == 19
            assert (await service_get(b,url, params={'channel': 'host'})).json()['messages'] == []
            assert (await service_get(outside,url, params={'channel': 'host'})).status_code == 403
            assert (await service_get(a,url, params={'channel': 'host', 'participant_user_id': ids[2]})).status_code == 403
            for params in ({'channel': 'host', 'before_sequence': 2, 'after_sequence': 0},
                           {'channel': 'common', 'participant_user_id': ids[1]}, {'channel': 'host', 'team_label': 'Wolf team'}):
                assert (await service_get(host,url, params=params)).status_code == 422
            before = (await service_get(a,url, params={'channel': 'host'})).json()
            general = (await service_get(a,url, params={'channel': 'common'})).json()
            activity = (await service_get(a,base + '/chat/context')).json()['activity']
            await post(host, base + f'/game/players/{ids[2]}/private-messages', command(text='Secret for B'))
            await post(host, base + f'/game/players/{ids[2]}/actions', command(action='note', text='Host note'))
            assert (await service_get(a,url, params={'channel': 'host'})).json() == before
            assert (await service_get(a,url, params={'channel': 'common'})).json() == general
            assert (await service_get(a,base + '/chat/context')).json()['activity'] == activity
            # Update an old private source outside the newest page; changes includes its stable ID.
            revision = base + f'/game/content/{sources[0]}/revisions'
            await post(host, revision, command(action='edit', text='Edited old private'))
            changes = (await service_get(a,url, params={'channel': 'host', 'after_updated_sequence': before['room_cursor']})).json()
            assert len(changes['messages']) == 1
            assert changes['messages'][0]['event_id'] == sources[0]
            assert changes['messages'][0]['text'] == 'Edited old private'
            assert changes['messages'][0]['source_kind'] == 'private_message'
            assert (await service_get(a,base + '/game/private-messages')).json()[0]['text'] == 'Edited old private'
            await post(host, revision, command(action='delete'))
            tombstone = (await service_get(a,url, params={'channel': 'host', 'after_updated_sequence': changes['room_cursor']})).json()
            assert len(tombstone['messages']) == 1 and tombstone['messages'][0]['deleted']
            assert tombstone['messages'][0]['text'] == ''
            assert [item['text'] for item in (await service_get(b,url, params={'channel': 'host', 'after_updated_sequence': 0})).json()['messages']] == ['Secret for B']
            await post(host, base + '/game/finish', {'result_label': 'Done', 'result_reason': 'Verify transcript'})
            finished = (await service_get(b,url, params={'channel': 'host', 'participant_user_id': ids[1], 'after_sequence': 0, 'limit': 50})).json()['messages']
            assert [item['event_id'] for item in finished] == expected
            assert finished[0]['deleted'] and finished[0]['text'] == ''
            assert [item['action'] for item in finished[0]['history']] == ['send', 'edit', 'delete']
            assert finished[0]['history'][0]['text'] == 'Private 0'
            assert len((await service_get(b,base + '/chat/context')).json()['dm_participants']) == 3
            assert (await service_get(outside,url, params={'channel': 'host'})).status_code == 403
    asyncio.run(run())


def test_team_snapshot_and_departed_dm_catalog():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, *players, outside = clients
            assignments = (await service_get(host,base + '/roles/assignments')).json()
            by_id = dict(zip(ids[1:4], players))
            wolves = [item['user_id'] for item in assignments if item['role']['team_chat_label']]
            villager = next(item['user_id'] for item in assignments if not item['role']['team_chat_label'])
            a, b = wolves
            url = base + '/chat/timeline'
            await post(host, base + '/game/advance', {'expected_phase_type': 'day', 'expected_round_number': 0})
            await post(by_id[a], base + '/chat/messages', command(channel='team', text='Old team history'))
            assert len((await service_get(by_id[b],url, params={'channel': 'team', 'team_label': 'Wolf team'})).json()['messages']) == 1
            await post(host, base + f'/game/players/{b}/actions', command(action='change_team', text='New team'))
            await post(by_id[a], base + '/chat/messages', command(channel='team', text='After leaving'))
            await post(host, base + f'/game/players/{villager}/actions', command(action='change_team', text='Wolf team'))
            assert [m['text'] for m in (await service_get(by_id[b],url, params={'channel': 'team', 'team_label': 'Wolf team'})).json()['messages']] == ['Old team history']
            assert (await service_get(by_id[villager],url, params={'channel': 'team', 'team_label': 'Wolf team'})).json()['messages'] == []
            context = (await service_get(by_id[b],base + '/chat/context')).json()
            assert 'Wolf team' in context['team_labels'] and 'Wolf team' not in context['writable_team_labels']
            assert context['dm_participants'] == []
            sent = await post(host, base + f'/game/players/{b}/private-messages', command(text='Retain after kick'))
            await post(host, base + f'/players/{b}/kick', command(reason='Test retained history'))
            assert (await service_get(by_id[b],url, params={'channel': 'host'})).status_code == 403
            host_context = (await service_get(host,base + '/chat/context')).json()
            assert any(item['user_id'] == b and item['departed'] for item in host_context['dm_participants'])
            assert [m['event_id'] for m in (await service_get(host,url, params={'channel': 'host', 'participant_user_id': b})).json()['messages']] == [sent['event_id']]
            response = await host.post(base + '/chat/messages', json=command(channel='host', participant_user_id=b, text='Cannot send to departed'), headers={'Origin': ORIGIN})
            assert response.status_code == 409
            await post(host, base + '/game/finish', {'result_label': 'Done', 'result_reason': 'Verify expanded access'})
            finished_context = (await service_get(by_id[villager],base + '/chat/context')).json()
            assert 'New team' in finished_context['team_labels'] or 'Wolf team' in finished_context['team_labels']
            assert any(item['user_id'] == b and item['departed'] for item in finished_context['dm_participants'])
            assert len((await service_get(by_id[b],url, params={'channel': 'team', 'team_label': 'Wolf team'})).json()['messages']) == 2
            assert len((await service_get(by_id[villager],url, params={'channel': 'host', 'participant_user_id': b})).json()['messages']) == 1
    asyncio.run(run())


def test_private_source_live_create_edit_delete_remain_scoped(monkeypatch):
    from test_live import ASGISocket
    from app import live
    monkeypatch.setattr(live, 'POLL_INTERVAL_SECONDS', .02)

    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, *_ = clients
            sockets = []
            try:
                for client in (host, a, b):
                    socket = ASGISocket(base + '/live', client.cookies.get('ma_soi_session'), service_protocol=True)
                    assert (await socket.connect())['type'] == 'websocket.accept'
                    sockets.append(socket)
                sent = await post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='Only host and A'))
                for socket in sockets[:2]:
                    assert (await socket.event(sent['event_id']))['kind'] == 'player.private_message_sent'
                for action in ('edit', 'delete'):
                    revision = await post(host, base + f"/game/content/{sent['event_id']}/revisions", command(
                        action=action, **({'text': 'Edited only for A'} if action == 'edit' else {})))
                    assert revision['public_event_id'] is None
                    async with session_factory()() as db:
                        message = await db.scalar(select(PrivateMessage).where(PrivateMessage.event_id == UUID(sent['event_id'])))
                        event = await db.scalar(select(GameEvent).where(GameEvent.room_id == room_id, GameEvent.sequence == message.updated_sequence))
                        event_id = str(event.id)
                    for socket in sockets[:2]:
                        frame = await socket.event(event_id)
                        assert frame['kind'] == 'chat.revised'
                        assert set(frame) == {'type', 'event_id', 'sequence', 'kind'}
                    try:
                        await sockets[2].event(event_id, timeout=.1)
                        assert False, 'Private edit/delete invalidation reached another player'
                    except asyncio.TimeoutError:
                        pass
                    changes = (await service_get(a,base + '/chat/timeline', params={'channel': 'host', 'after_updated_sequence': 0})).json()['messages']
                    assert len(changes) == 1 and changes[0]['deleted'] == (action == 'delete')
            finally:
                for socket in sockets:
                    await socket.close()
    asyncio.run(run())


def test_private_revision_rolls_back_if_delivery_event_fails(monkeypatch):
    import pytest
    from app import content_revisions
    from app.db.models import ContentRevision

    async def run():
        async with match() as (clients, ids, base, room_id):
            host = clients[0]
            sent = await post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='Original atomic source'))
            original_append = content_revisions.append_event

            async def fail_delivery(*args, **kwargs):
                if args[3] == 'chat.revised':
                    raise RuntimeError('private timeline delivery failed')
                return await original_append(*args, **kwargs)

            monkeypatch.setattr(content_revisions, 'append_event', fail_delivery)
            body = command(action='edit', text='Atomic edited source')
            url = base + f"/game/content/{sent['event_id']}/revisions"
            with pytest.raises(RuntimeError, match='private timeline delivery failed'):
                await host.post(url, json=body, headers={'Origin': ORIGIN})
            async with session_factory()() as db:
                assert await db.get(ContentRevision, UUID(sent['event_id'])) is None
                private = await db.scalar(select(PrivateMessage).where(PrivateMessage.event_id == UUID(sent['event_id'])))
                assert private.updated_sequence == private.sequence
            monkeypatch.setattr(content_revisions, 'append_event', original_append)
            assert not (await post(host, url, body))['repeated']
            item = (await service_get(clients[1],base + '/chat/timeline', params={'channel': 'host'})).json()['messages'][0]
            assert item['text'] == 'Atomic edited source' and item['updated_sequence'] > item['sequence']
    asyncio.run(run())
