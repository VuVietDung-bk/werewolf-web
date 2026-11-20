"""Stable channel IDs, scoped discovery and addressed sends under current room policy."""
import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app import channels
from app.db.events import append_event
from app.db.models import ChatMessage, GameEvent, Room, RoomChannel
from app.db.session import session_factory
from test_chat_timeline import match, post, command, ORIGIN


def v2(base):
    return base.replace('/api/rooms/', '/api/v2/rooms/') + '/channels'


async def catalog(client, base):
    response = await client.get(v2(base))
    assert response.status_code == 200, response.text
    return response.json()['channels']


def find(items, kind, **props):
    return next(item for item in items if item['kind'] == kind and all(item.get(key) == value for key, value in props.items()))


async def addressed(client, base, channel, body):
    return await client.post(v2(base) + '/' + channel['id'] + '/messages', json=body, headers={'Origin': ORIGIN})


def test_channel_catalog_ids_permissions_and_idempotency():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, _, outside = clients
            async with session_factory()() as db:
                version = (await db.get(Room, room_id)).version
                count = await db.scalar(select(func.count()).select_from(RoomChannel).where(RoomChannel.room_id == room_id))
            host_catalog = await catalog(host, base)
            assert host_catalog == await catalog(host, base)
            assert len({item['id'] for item in host_catalog}) == len(host_catalog)
            assert len([item for item in host_catalog if item['kind'] == 'moderator_dm']) == 3
            own = find(await catalog(a, base), 'moderator_dm')
            assert own['participant_user_id'] == ids[1] and not own['capabilities']['send']
            assert all(item['participant_user_id'] in (None, ids[1]) for item in await catalog(a, base))
            for item in host_catalog:
                assert item['capabilities']['react'] == (item['capabilities']['send'] and item['kind'] in {'common','team','moderator_dm'})
                assert not any(key in item for key in ('sequence', 'version', 'recipient_ids', 'event_id'))
            assert (await outside.get(v2(base))).status_code == 403
            peer = find(host_catalog, 'moderator_dm', participant_user_id=ids[1])
            body = command(text='Host opens selected DM')
            denied = await addressed(b, base, peer, body)
            assert denied.status_code == 404
            assert (await b.get(base + '/chat/timeline', params={'channel': 'host', 'channel_id': peer['id']})).status_code == 426
            async with session_factory()() as db:
                assert (await db.get(Room, room_id)).version == version
                assert await db.scalar(select(func.count()).select_from(RoomChannel).where(RoomChannel.room_id == room_id)) == count
            sent = await addressed(host, base, peer, body)
            assert sent.status_code == 200, sent.text
            assert set(sent.json()) == {'id', 'source_kind', 'repeated'}
            assert not sent.json()['repeated']
            assert (await addressed(host, base, peer, body)).json()['repeated']
            assert (await addressed(host, base, peer, {**body, 'text': 'Different'})).status_code == 409
            assert find(await catalog(a, base), 'moderator_dm')['capabilities']['send']
            reply_body = command(text='Reply through own channel')
            reply = await addressed(a, base, own, reply_body)
            assert reply.status_code == 200
            response = await a.get(v2(base)+'/'+own['id']+'/timeline')
            assert [item['text'] for item in response.json()['items']] == ['Host opens selected DM', 'Reply through own channel']
            assert (await a.get(base + '/chat/timeline', params={'channel': 'common', 'channel_id': own['id']})).status_code == 426
            assert (await addressed(host, base, find(host_catalog, 'vote'), command(text='Not a ballot'))).status_code == 409
            # Same key in a different room has a different ID and is never addressable through this room.
            other_room = await post(outside, '/api/rooms', {'name': 'Other channel scope', 'seat_limit': 2})
            try:
                other_base = '/api/rooms/' + other_room['id']
                other_common = find(await catalog(outside, other_base), 'common')
                assert other_common['id'] != find(host_catalog, 'common')['id']
                assert (await addressed(host, base, other_common, command(text='Cross room'))).status_code == 404
            finally:
                async with session_factory()() as db:
                    await db.delete(await db.get(Room, UUID(other_room['id'])))
                    await db.commit()
            old_body = command(channel='common', text='Legacy ACK before upgrade')
            await post(a, base + '/chat/messages', old_body)
            upgraded_retry = await addressed(a, base, find(await catalog(a, base), 'common'),
                {'client_request_id':old_body['client_request_id'], 'text':old_body['text']})
            assert upgraded_retry.status_code == 200 and upgraded_retry.json()['repeated']
            await post(host, base + '/game/pause', {'reason': 'Check current policy'})
            assert not find(await catalog(a, base), 'moderator_dm')['capabilities']['send']
            # A committed command is still acknowledged during a later read-only phase.
            assert (await addressed(a, base, own, reply_body)).json()['repeated']
    asyncio.run(run())


def test_team_ids_bind_send_target_and_policy_changes():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, *players, _outside = clients
            assignments = (await host.get(base + '/roles/assignments')).json()
            wolves = [item['user_id'] for item in assignments if item['role']['team_chat_label']]
            actor = dict(zip(ids[1:4], players))[wolves[0]]
            other = dict(zip(ids[1:4], players))[wolves[1]]
            wolf = find(await catalog(actor, base), 'team')
            assert not wolf['capabilities']['send'] and 'ban đêm' in wolf['readonly_reason']
            await post(host, base + '/game/advance', {'expected_phase_type': 'day', 'expected_round_number': 0})
            assert find(await catalog(actor, base), 'team')['id'] == wolf['id']
            body = command(text='Send to original team')
            assert (await addressed(actor, base, wolf, body)).status_code == 200
            before_other = await catalog(other, base)
            await post(host, base + f'/game/players/{wolves[0]}/actions', command(action='change_team', text='New named team'))
            old = find(await catalog(actor, base), 'team', team_label='Wolf team')
            new = find(await catalog(actor, base), 'team', team_label='New named team')
            assert old['id'] == wolf['id'] and not old['capabilities']['send']
            assert 'đội cũ' in old['readonly_reason']
            assert new['id'] != old['id'] and new['capabilities']['send']
            assert (await addressed(actor, base, old, command(text='Stale channel'))).status_code == 403
            assert (await addressed(actor, base, old, body)).json()['repeated']
            fresh = await addressed(actor, base, new, command(text='Only new team'))
            assert fresh.status_code == 200
            async with session_factory()() as db:
                stored = await db.get(ChatMessage, UUID(fresh.json()['id']))
                assert stored.team_label == 'New named team'
            assert not (await other.get(base + '/chat/timeline', params={'channel': 'team', 'channel_id': new['id']})).status_code == 200
            assert await catalog(other, base) == before_other
            mute_url = base + f'/chat/players/{wolves[0]}/mute'
            response = await host.patch(mute_url, json=command(muted=True, reason='Test catalog mute'), headers={'Origin': ORIGIN})
            assert response.status_code == 200
            assert all(not item['capabilities']['send'] for item in await catalog(actor, base))
            response = await host.patch(mute_url, json=command(muted=False, reason='Test catalog unmute'), headers={'Origin': ORIGIN})
            assert response.status_code == 200
            await post(host, base + f'/game/players/{wolves[0]}/actions', command(action='kill'))
            assert not find(await catalog(actor, base), 'team', team_label='New named team')['capabilities']['send']
            assert await catalog(other, base) == before_other
            await post(host, base + '/game/finish', {'result_label': 'Done', 'result_reason': 'Expanded channel discovery'})
            after = await catalog(other, base)
            assert find(after, 'team', team_label='New named team')['id'] == new['id']
            assert len([item for item in after if item['kind'] == 'moderator_dm']) == 3
            assert all(not item['capabilities']['send'] for item in after)
    asyncio.run(run())


def test_waiting_send_rechecks_phase_after_room_lock(monkeypatch):
    async def run():
        async with match() as (clients, ids, base, room_id):
            actor = clients[1]
            common = find(await catalog(actor, base), 'common')
            authorized = asyncio.Event()
            original_access = channels.require_room_access

            async def signal_access(*args, **kwargs):
                result = await original_access(*args, **kwargs)
                authorized.set()
                return result

            monkeypatch.setattr(channels, 'require_room_access', signal_access)
            body = command(text='Race with night transition')
            async with session_factory()() as db:
                room = await db.scalar(select(Room).where(Room.id == room_id).with_for_update())
                pending = asyncio.create_task(addressed(actor, base, common, body))
                await asyncio.wait_for(authorized.wait(), timeout=2)
                room.phase_type = 'night'
                room.round_number = 1
                await append_event(db, room_id, UUID(ids[0]), 'game.phase_advanced', 'room', {'current': {'phase_type':'night','round_number':1}})
                await db.commit()
            response = await asyncio.wait_for(pending, timeout=3)
            assert response.status_code == 403, response.text
            async with session_factory()() as db:
                assert await db.scalar(select(GameEvent.id).where(GameEvent.room_id == room_id,
                    GameEvent.client_request_id == UUID(body['client_request_id']))) is None
    asyncio.run(run())


def test_channel_registration_rolls_back_with_source_event(monkeypatch):
    from app.db import events

    async def run():
        async with match() as (clients, ids, base, room_id):
            original = events.register_for_event

            async def fail_registry(db, room_id, kind, payload):
                await original(db, room_id, kind, payload)
                if kind == 'player.change_team':
                    raise RuntimeError('registry transaction failed')

            monkeypatch.setattr(events, 'register_for_event', fail_registry)
            body = command(action='change_team', text='Rolled back team')
            with pytest.raises(RuntimeError, match='registry transaction failed'):
                await clients[0].post(base + f'/game/players/{ids[1]}/actions', json=body, headers={'Origin': ORIGIN})
            async with session_factory()() as db:
                assert await db.scalar(select(RoomChannel.id).where(RoomChannel.room_id == room_id,
                    RoomChannel.channel_key == 'Rolled back team')) is None
                assert await db.scalar(select(GameEvent.id).where(GameEvent.room_id == room_id,
                    GameEvent.client_request_id == UUID(body['client_request_id']))) is None
    asyncio.run(run())
