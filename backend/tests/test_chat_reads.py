"""Read state is actor-scoped, monotonic, creation-only and race-safe."""
import asyncio
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, update

from app import chat, chat_reads
from app.db.models import ChannelRead, ChatMessage, Outbox, PrivateMessage, Room, RoomDelivery, User
from app.db.session import session_factory
from app.main import app
from test_chat_timeline import match, post, command, ORIGIN
from test_channels import addressed, catalog, find, v2
from test_room_delivery import sync


async def read(client, base, channel, item):
    return await client.post(v2(base) + '/' + channel['id'] + '/read', json={'visible_item_id': item}, headers={'Origin': ORIGIN})


def test_reads_persist_without_event_delivery_or_other_actor_change():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, *_ = clients
            common = find(await catalog(a, base), 'common')
            assert not common['unread']
            ack = (await addressed(host, base, common, command(text='Incoming'))).json()
            assert find(await catalog(a, base), 'common')['unread']
            assert not find(await catalog(host, base), 'common')['unread']  # own message
            before_a, before_b, before_host = [await sync(c, base) for c in (a, b, host)]
            async with session_factory()() as db:
                version = (await db.get(Room, room_id)).version
                outbox = await db.scalar(select(func.count()).select_from(Outbox))
            assert (await read(a, base, common, 'chat:' + ack['id'])).status_code == 204
            assert not find(await catalog(a, base), 'common')['unread']
            assert find(await catalog(b, base), 'common')['unread']
            for client, snapshot in zip((a, b, host), (before_a, before_b, before_host)):
                assert await sync(client, base, snapshot['next_cursor']) == {
                    'deliveries': [], 'next_cursor': snapshot['next_cursor'], 'has_more': False}
            async with session_factory()() as db:
                assert (await db.get(Room, room_id)).version == version
                assert await db.scalar(select(func.count()).select_from(Outbox)) == outbox
                saved = await db.get(ChannelRead, (room_id, UUID(ids[1]), UUID(common['id'])))
                assert saved and saved.position > 0
                count = await db.scalar(select(func.count()).select_from(ChannelRead))
            await catalog(a, base);await catalog(b, base)
            async with session_factory()() as db:
                assert await db.scalar(select(func.count()).select_from(ChannelRead)) == count
    asyncio.run(run())


def test_mixed_dm_prefixes_concurrent_tabs_never_regress(monkeypatch):
    monkeypatch.setattr(chat, 'CHAT_BURST_LIMIT', 100)
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            dm = find(await catalog(host, base), 'moderator_dm', participant_user_id=ids[1])
            source = await post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='One-way'))
            chat_ack = (await addressed(host, base, dm, command(text='Two-way'))).json()
            async with session_factory()() as db:
                private = await db.scalar(select(PrivateMessage).where(PrivateMessage.event_id == UUID(source['event_id'])))
                private_id = private.id
                await db.execute(update(ChatMessage).where(ChatMessage.id == UUID(chat_ack['id'])).values(id=private_id))
                await db.commit()
                expected = await db.scalar(select(RoomDelivery.position).where(RoomDelivery.room_id == room_id,
                    RoomDelivery.user_id == UUID(ids[1]), RoomDelivery.source_event_id == (
                        select(ChatMessage.event_id).where(ChatMessage.id == private_id).scalar_subquery())))
            own = find(await catalog(a, base), 'moderator_dm')
            results = await asyncio.gather(*(read(a, base, own, kind + ':' + str(private_id))
                for kind in ['chat', 'private_message', 'private_message', 'chat']))
            assert all(item.status_code == 204 for item in results)
            async with session_factory()() as db:
                assert (await db.get(ChannelRead, (room_id, UUID(ids[1]), UUID(own['id'])))).position == expected
            assert not find(await catalog(a, base), 'moderator_dm')['unread']
    asyncio.run(run())


def test_edits_do_not_reopen_unread_and_deletes_do_not_hide_earlier_unread():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            common = find(await catalog(host, base), 'common')
            one = (await addressed(host, base, common, command(text='Read this'))).json()
            assert (await read(a, base, common, 'chat:' + one['id'])).status_code == 204
            await post(host, v2(base) + f"/{common['id']}/items/chat/{one['id']}/revisions", command(action='edit', text='Edited read item'))
            assert not find(await catalog(a, base), 'common')['unread']
            two = (await addressed(host, base, common, command(text='Earlier unread'))).json()
            three = (await addressed(host, base, common, command(text='Latest unread'))).json()
            await post(host, v2(base) + f"/{common['id']}/items/chat/{three['id']}/revisions", command(action='delete'))
            assert find(await catalog(a, base), 'common')['unread']
            assert (await read(a, base, common, 'chat:' + three['id'])).status_code == 409
            assert (await read(a, base, common, 'chat:' + two['id'])).status_code == 204
            assert not find(await catalog(a, base), 'common')['unread']
    asyncio.run(run())


def test_read_scope_extra_fields_and_postgame_history():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, _, outside = clients
            dm = find(await catalog(host, base), 'moderator_dm', participant_user_id=ids[1])
            item = (await addressed(host, base, dm, command(text='Only An sees'))).json()
            common = find(await catalog(a, base), 'common')
            for client, channel in [(b, dm), (a, common), (outside, dm)]:
                assert (await read(client, base, channel, 'chat:' + item['id'])).status_code in (403, 404)
            assert (await read(a, base, dm, 'chat:' + str(uuid4()))).status_code == 404
            response = await a.post(v2(base) + f"/{dm['id']}/read", json={'visible_item_id': 'chat:' + item['id'], 'position': 999999}, headers={'Origin': ORIGIN})
            assert response.status_code == 422
            assert (await read(a, base, dm, 'not-a-source')).status_code == 422
            assert (await read(a, base, find(await catalog(a, base), 'vote'), 'chat:' + item['id'])).status_code == 409
            await post(host, base + '/game/finish', {'result_label': 'Done', 'result_reason': 'Expanded read'})
            assert not any(channel['unread'] for channel in await catalog(b, base))
            assert (await read(b, base, dm, 'chat:' + item['id'])).status_code == 204
            async with session_factory()() as db:
                assert await db.get(ChannelRead, (room_id, UUID(ids[2]), UUID(dm['id']))) is None
    asyncio.run(run())


def test_team_history_rejects_new_member_ack_but_keeps_former_recipient():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, *players, _ = clients
            assignments = (await host.get(base + '/roles/assignments')).json()
            wolves = [row['user_id'] for row in assignments if row['role']['team_chat_label']]
            villager = next(row['user_id'] for row in assignments if not row['role']['team_chat_label'])
            actors = dict(zip(ids[1:4], players))
            await post(host, base + '/game/advance', {'expected_phase_type': 'day', 'expected_round_number': 0})
            team = find(await catalog(host, base), 'team')
            item = (await addressed(host, base, team, command(text='Before joining team'))).json()
            await post(host, base + f'/game/players/{villager}/actions', command(action='change_team', text='Wolf team'))
            assert not find(await catalog(actors[villager], base), 'team')['unread']
            assert (await read(actors[villager], base, team, 'chat:' + item['id'])).status_code == 404
            await post(host, base + f'/game/players/{wolves[0]}/actions', command(action='change_team', text='Another team'))
            former = find(await catalog(actors[wolves[0]], base), 'team', team_label='Wolf team')
            assert former['unread']
            assert (await read(actors[wolves[0]], base, former, 'chat:' + item['id'])).status_code == 204
            assert not find(await catalog(actors[wolves[0]], base), 'team', team_label='Wolf team')['unread']
    asyncio.run(run())


def test_arrival_during_ack_is_not_marked_read(monkeypatch):
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            common = find(await catalog(a, base), 'common')
            item = (await addressed(host, base, common, command(text='Visible old item'))).json()
            entered, release = asyncio.Event(), asyncio.Event()
            original = chat_reads.advance_read
            async def delayed(*args):
                entered.set();await release.wait();return await original(*args)
            with monkeypatch.context() as patch:
                patch.setattr(chat_reads, 'advance_read', delayed)
                pending = asyncio.create_task(read(a, base, common, 'chat:' + item['id']))
                await asyncio.wait_for(entered.wait(), timeout=2)
                await addressed(host, base, common, command(text='Arrived while ACK was pending'))
                release.set()
                assert (await asyncio.wait_for(pending, timeout=2)).status_code == 204
            assert find(await catalog(a, base), 'common')['unread']
    asyncio.run(run())


def test_kick_during_ack_rolls_back_checkpoint(monkeypatch):
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            common = find(await catalog(a, base), 'common')
            item = (await addressed(host, base, common, command(text='Before kick'))).json()
            entered, release = asyncio.Event(), asyncio.Event()
            original = chat_reads.advance_read
            async def delayed(*args):
                entered.set();await release.wait();return await original(*args)
            with monkeypatch.context() as patch:
                patch.setattr(chat_reads, 'advance_read', delayed)
                pending = asyncio.create_task(read(a, base, common, 'chat:' + item['id']))
                await asyncio.wait_for(entered.wait(), timeout=2)
                await post(host, base + f'/players/{ids[1]}/kick', command(reason='Pending ACK'))
                release.set()
                assert (await asyncio.wait_for(pending, timeout=2)).status_code == 403
            async with session_factory()() as db:
                assert await db.get(ChannelRead, (room_id, UUID(ids[1]), UUID(common['id']))) is None
    asyncio.run(run())


def test_public_history_before_joining_cannot_ack_future_deliveries():
    async def run():
        async with match() as (clients, ids, base, room_id):
            outside = clients[-1]
            other_room = await post(outside, '/api/rooms', {'name': 'Prejoin history', 'seat_limit': 2})
            other_base = '/api/rooms/' + other_room['id']
            new_user = None
            try:
                common = find(await catalog(outside, other_base), 'common')
                old = (await addressed(outside, other_base, common, command(text='Before joining'))).json()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as newcomer:
                    new_user = (await post(newcomer, '/api/auth/guest', {'display_name': 'New reader'}))['id']
                    await post(newcomer, '/api/rooms/join', {'code': other_room['code'], 'accept_postgame_transcript': True})
                    assert not find(await catalog(newcomer, other_base), 'common')['unread']
                    assert (await read(newcomer, other_base, common, 'chat:' + old['id'])).status_code == 204
                    async with session_factory()() as db:
                        assert await db.get(ChannelRead, (UUID(other_room['id']), UUID(new_user), UUID(common['id']))) is None
                    await addressed(outside, other_base, common, command(text='New delivery after joining'))
                    assert (await read(newcomer, other_base, common, 'chat:' + old['id'])).status_code == 204
                    assert find(await catalog(newcomer, other_base), 'common')['unread']
                    original_common = find(await catalog(clients[0], base), 'common')
                    original = (await addressed(clients[0], base, original_common, command(text='Other room source'))).json()
                    assert (await read(outside, other_base, common, 'chat:' + original['id'])).status_code == 404
            finally:
                async with session_factory()() as db:
                    await db.delete(await db.get(Room, UUID(other_room['id'])))
                    await db.flush()
                    if new_user:
                        await db.delete(await db.get(User, UUID(new_user)))
                    await db.commit()
    asyncio.run(run())
