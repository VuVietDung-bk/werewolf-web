"""Recipient snapshots, transactionality and opaque HTTP recovery under races."""
import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.db.events import append_event
from app.db.models import GameEvent, Outbox, Room, RoomDelivery, RoomDeliveryStream
from app.db.session import session_factory
from test_chat_timeline import match, post, command, ORIGIN
from test_channels import addressed, catalog, find


def sync_url(base):
    return base.replace('/api/rooms/', '/api/v2/rooms/') + '/sync'


def assert_safe(value):
    if isinstance(value, dict):
        assert not {'sequence', 'position', 'event_id', 'source_event_id', 'kind', 'payload',
                    'policy_epoch', 'recipient_ids', 'user_id', 'version'} & value.keys(), value
        for child in value.values():
            assert_safe(child)
    elif isinstance(value, list):
        for child in value:
            assert_safe(child)


async def sync(client, base, cursor=None, **params):
    response = await client.get(sync_url(base), params={**({'cursor': cursor} if cursor else {}), **params})
    assert response.status_code == 200, response.text[:400]
    page = response.json()
    assert_safe(page)
    assert len(page['next_cursor']) == 114
    return page


async def drain(client, base, cursor, limit=2):
    deliveries = []
    for _ in range(100):
        page = await sync(client, base, cursor, limit=limit)
        deliveries.extend(page['deliveries'])
        cursor = page['next_cursor']
        if not page['has_more']:
            return deliveries, cursor
    raise AssertionError('feed did not converge')


def test_private_activity_does_not_touch_other_stream_and_get_is_read_only():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, _, _ = clients
            before_a, before_b, before_host = [await sync(c, base) for c in (a, b, host)]
            await post(host, base + f'/game/players/{ids[2]}/private-messages', command(text='B private'))
            await post(b, base + '/chat/messages', command(channel='host', text='B reply'))
            await post(host, base + f'/game/players/{ids[2]}/actions', command(action='note', text='B private note'))
            vote = await post(host, base + '/votes', command())
            # Opening a vote is public; establish a new comparison boundary.
            stable = await sync(a, base)
            await post(b, base + f"/votes/{vote['vote_round_id']}/ballots", command(choice='skip'))
            async with session_factory()() as db:
                await append_event(db, room_id, UUID(ids[2]), 'role.request_submitted', 'host',
                    {'request': {'user_id': ids[2], 'text': 'Private request'}})
                await append_event(db, room_id, UUID(ids[0]), 'future.unknown_private', 'host',
                    {'request': {'user_id': ids[1]}, 'text': 'Not a player event'})
                await db.commit()
            untouched = await sync(a, base, stable['next_cursor'])
            assert untouched == {'deliveries': [], 'next_cursor': stable['next_cursor'], 'has_more': False}
            all_a, _ = await drain(a, base, before_a['next_cursor'])
            assert len(all_a) == 1  # public vote open only
            got_b, _ = await drain(b, base, before_b['next_cursor'])
            assert len(got_b) == 5  # DM send/reply, public open, own ballot/request; no note
            got_host, _ = await drain(host, base, before_host['next_cursor'])
            assert len(got_host) == 7
            async with session_factory()() as db:
                count = await db.scalar(select(func.count()).select_from(RoomDelivery).where(RoomDelivery.room_id == room_id))
                heads = (await db.execute(select(RoomDeliveryStream.user_id, RoomDeliveryStream.position,
                    RoomDeliveryStream.policy_epoch).where(RoomDeliveryStream.room_id == room_id))).all()
            await sync(a, base)
            await sync(a, base, untouched['next_cursor'])
            async with session_factory()() as db:
                assert count == await db.scalar(select(func.count()).select_from(RoomDelivery).where(RoomDelivery.room_id == room_id))
                assert heads == (await db.execute(select(RoomDeliveryStream.user_id, RoomDeliveryStream.position,
                    RoomDeliveryStream.policy_epoch).where(RoomDeliveryStream.room_id == room_id))).all()
    asyncio.run(run())


def test_pagination_dedup_source_revisions_and_concurrent_retry():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, _, _ = clients
            start = await sync(a, base)
            peer = find(await catalog(host, base), 'moderator_dm', participant_user_id=ids[1])
            body = command(text='Exactly once')
            results = await asyncio.gather(*(addressed(host, base, peer, body) for _ in range(4)))
            assert all(result.status_code == 200 for result in results)
            assert len({result.json()['id'] for result in results}) == 1
            source_id = results[0].json()['id']
            await post(host, base.replace('/api/rooms/', '/api/v2/rooms/') +
                f"/channels/{peer['id']}/items/chat/{source_id}/revisions", command(action='edit', text='Updated'))
            await post(host, base.replace('/api/rooms/', '/api/v2/rooms/') +
                f"/channels/{peer['id']}/items/chat/{source_id}/revisions", command(action='delete'))
            await post(a, base + '/chat/messages', command(channel='common', text='Public emoji 🌙'))
            deliveries, cursor = await drain(a, base, start['next_cursor'], limit=1)
            assert len(deliveries) == len({item['id'] for item in deliveries}) == 4
            assert all(item['channel_ids'] == [peer['id']] for item in deliveries[:3])
            assert await sync(a, base, cursor) == {'deliveries': [], 'next_cursor': cursor, 'has_more': False}
            replay, _ = await drain(a, base, start['next_cursor'], limit=3)
            assert replay == deliveries
            async with session_factory()() as db:
                records = (await db.scalars(select(RoomDelivery).where(RoomDelivery.room_id == room_id,
                    RoomDelivery.user_id == UUID(ids[1])).order_by(RoomDelivery.position))).all()
                assert [item.position for item in records] == list(range(1, len(records) + 1))
                assert len({item.source_event_id for item in records}) == len(records)
    asyncio.run(run())


def test_team_change_old_history_and_kick_replay_boundary():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, *players, _outside = clients
            actors = dict(zip(ids[1:4], players))
            assignments = (await host.get(base + '/roles/assignments')).json()
            wolves = [item['user_id'] for item in assignments if item['role']['team_chat_label']]
            a, b = (actors[uid] for uid in wolves)
            await post(host, base + '/game/advance', {'expected_phase_type': 'day', 'expected_round_number': 0})
            wolf = find(await catalog(host, base), 'team')
            old_a = await sync(a, base)
            old_b = await sync(b, base)
            await addressed(host, base, wolf, command(text='Both wolves received'))
            await post(host, base + f'/game/players/{wolves[0]}/actions', command(action='change_team', text='New team'))
            assert (await a.get(sync_url(base), params={'cursor': old_a['next_cursor']})).status_code == 409
            visible_b, _ = await drain(b, base, old_b['next_cursor'])
            assert len(visible_b) == 1 and visible_b[0]['channel_ids'] == [wolf['id']]
            a_new, b_new = await sync(a, base), await sync(b, base)
            await addressed(host, base, wolf, command(text='Only remaining wolf'))
            assert not (await sync(a, base, a_new['next_cursor']))['deliveries']
            new_team = find(await catalog(host, base), 'team', team_label='New team')
            await addressed(host, base, new_team, command(text='New team only'))
            a_deliveries, _ = await drain(a, base, a_new['next_cursor'])
            assert len(a_deliveries) == 1 and a_deliveries[0]['channel_ids'] == [new_team['id']]
            b_deliveries, _ = await drain(b, base, b_new['next_cursor'])
            assert len(b_deliveries) == 1 and b_deliveries[0]['channel_ids'] == [wolf['id']]
            await post(host, base + f'/players/{wolves[0]}/kick', command(reason='Test revoke'))
            assert (await a.get(sync_url(base), params={'cursor': a_new['next_cursor']})).status_code == 403
            await post(host, base + '/game/finish', {'result_label': 'Done', 'result_reason': 'Test expanded access'})
            assert (await a.get(sync_url(base), params={'cursor': a_new['next_cursor']})).status_code == 409
            assert (await b.get(sync_url(base), params={'cursor': b_new['next_cursor']})).status_code == 409
            assert (await a.get(sync_url(base))).status_code == 200
    asyncio.run(run())


def test_source_outbox_and_delivery_rollback_together(monkeypatch):
    from app.db import events

    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            original = events.record_deliveries
            async def broken(db, room, event):
                await original(db, room, event)
                raise RuntimeError('Injected delivery failure')
            before_a, before_host = await sync(a, base), await sync(host, base)
            async with session_factory()() as db:
                version = (await db.get(Room, room_id)).version
                count = await db.scalar(select(func.count()).select_from(Outbox))
            body = command(channel='common', text='Must roll back')
            with monkeypatch.context() as patch:
                patch.setattr(events, 'record_deliveries', broken)
                with pytest.raises(RuntimeError, match='Injected delivery failure'):
                    await post(host, base + '/chat/messages', body)
            assert not (await sync(a, base, before_a['next_cursor']))['deliveries']
            assert not (await sync(host, base, before_host['next_cursor']))['deliveries']
            async with session_factory()() as db:
                assert (await db.get(Room, room_id)).version == version
                assert await db.scalar(select(func.count()).select_from(Outbox)) == count
                assert await db.scalar(select(GameEvent).where(GameEvent.client_request_id == UUID(body['client_request_id']))) is None
            await post(host, base + '/chat/messages', body)
            assert len((await sync(a, base, before_a['next_cursor']))['deliveries']) == 1
    asyncio.run(run())


def test_sync_token_scope_auth_limits_and_restore():
    from app.room_sync import delivery_scope, LIVE_SCOPE
    from app.chat_cursors import cursor_codec
    from app.db.models import User

    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, _, outside = clients
            page = await sync(a, base)
            token = page['next_cursor']
            for client, candidate in [(a, 'x' * 10000), (a, token[:-5] + '!abcd'), (b, token)]:
                response = await client.get(sync_url(base), params={'cursor': candidate})
                assert response.status_code == 409 and candidate not in response.text
            own = find(await catalog(a, base), 'moderator_dm')
            response = await a.get(base.replace('/api/rooms/', '/api/v2/rooms/') +
                f"/channels/{own['id']}/changes", params={'cursor': token})
            assert response.status_code == 409
            other_room = await post(outside, '/api/rooms', {'name': 'Other feed', 'seat_limit': 2})
            try:
                response = await outside.get('/api/v2/rooms/' + other_room['id'] + '/sync', params={'cursor': token})
                assert response.status_code == 409
            finally:
                async with session_factory()() as db:
                    await db.delete(await db.get(Room, UUID(other_room['id'])))
                    await db.commit()
            assert (await outside.get(sync_url(base))).status_code == 403
            for params in ({'limit': 0}, {'limit': 101}, {'after_sequence': 0}):
                assert (await a.get(sync_url(base), params=params)).status_code == 422
            async with session_factory()() as db:
                user = await db.get(User, UUID(ids[1]))
                head, epoch = await delivery_scope(db, room_id, user)
                future = cursor_codec().encode(room_id, user.id, LIVE_SCOPE, 'room_live', epoch,
                    created=head + 1, updated=0)
            assert (await a.get(sync_url(base), params={'cursor': future})).status_code == 409
            assert (await a.post('/api/auth/logout', headers={'Origin': ORIGIN})).status_code == 204
            assert (await a.get(sync_url(base), params={'cursor': token})).status_code == 401
    asyncio.run(run())


def test_new_commit_after_high_water_is_left_for_next_page(monkeypatch):
    from app import room_sync

    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            page = await sync(a, base)
            captured, release = asyncio.Event(), asyncio.Event()
            original = room_sync.delivery_scope
            async def delayed(*args):
                result = await original(*args)
                captured.set()
                await release.wait()
                return result
            with monkeypatch.context() as patch:
                patch.setattr(room_sync, 'delivery_scope', delayed)
                pending = asyncio.create_task(sync(a, base, page['next_cursor']))
                await asyncio.wait_for(captured.wait(), timeout=2)
                await post(host, base + '/chat/messages', command(channel='common', text='Commit after boundary'))
                release.set()
                unchanged = await asyncio.wait_for(pending, timeout=2)
            assert unchanged['deliveries'] == [] and unchanged['next_cursor'] == page['next_cursor']
            assert len((await sync(a, base, unchanged['next_cursor']))['deliveries']) == 1
    asyncio.run(run())
