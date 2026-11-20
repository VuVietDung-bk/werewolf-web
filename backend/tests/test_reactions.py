"""Reaction state/history, scoped updates, idempotency and transaction races."""
import asyncio
from datetime import timedelta
from pathlib import Path
import re
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from app import reactions
from app.db.events import append_event
from app.db.models import ChatMessage, GameEvent, MessageReaction, Outbox, PrivateMessage, Room, RoomMember
from app.db.session import session_factory
from test_chat_timeline import match, post, command, ORIGIN
from test_channels import catalog, find, v2, addressed
from test_chat_reads import read
from test_chat_timeline_v2 import fetch, drain as drain_changes
from test_room_delivery import sync


def path(base, channel, kind, source_id):
    return v2(base) + f"/{channel['id']}/items/{kind}/{source_id}/reactions"


async def react(client, base, channel, source, emoji='👍', present=True, request_id=None):
    kind, source_id = source.split(':')
    return await client.request('PUT' if present else 'DELETE', path(base, channel, kind, source_id),
        json={'client_request_id': request_id or str(uuid4()), 'emoji': emoji}, headers={'Origin': ORIGIN})


def test_picker_catalog_and_complete_unicode_emoji():
    picker = Path(__file__).resolve().parents[2] / 'frontend/src/components/chat/emoji.ts'
    assert set(re.findall(r"\['([^']+)'", picker.read_text())) == reactions.ALLOWED_REACTIONS
    for emoji in reactions.ALLOWED_REACTIONS:
        assert reactions.ReactionInput(client_request_id=uuid4(), emoji=emoji).emoji == emoji
    for invalid in ['text', '<img onerror=1>', '👍👍', '👍 text', '', 'a', '1']:
        with pytest.raises(ValueError): reactions.ReactionInput(client_request_id=uuid4(), emoji=invalid)


def test_counts_concurrent_retry_changes_and_no_unread():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, *_ = clients
            common = find(await catalog(a, base), 'common')
            item = (await addressed(host, base, common, command(text='React here'))).json()
            source = 'chat:' + item['id']
            for client in (a, b): assert (await read(client, base, common, source)).status_code == 204
            url = v2(base) + '/' + common['id']
            start = await fetch(a, url + '/timeline')
            request_id = str(uuid4())
            results = await asyncio.gather(*(react(a, base, common, source, request_id=request_id) for _ in range(3)))
            assert all(result.status_code == 200 for result in results)
            assert (await react(b, base, common, source)).status_code == 200
            for client, mine in ((host, False), (a, True), (b, True)):
                page = await fetch(client, url + '/timeline')
                assert page['items'][0]['reactions'] == [{'emoji': '👍', 'count': 2, 'mine': mine}]
            inserts, changes, cursor = await drain_changes(a, url + '/changes', start['next_changes'])
            assert not inserts and changes[-1]['item']['reactions'][0]['count'] == 2
            assert not find(await catalog(a, base), 'common')['unread']
            assert not find(await catalog(b, base), 'common')['unread']
            assert (await react(a, base, common, source, present=False)).status_code == 200
            page = await fetch(a, url + '/timeline')
            assert page['items'][0]['reactions'] == [{'emoji': '👍', 'count': 1, 'mine': False}]
            checkpoint = page['next_changes']
            assert (await react(a, base, common, source, present=False)).status_code == 200  # logged no-op
            stable = await fetch(a, url + '/changes', cursor=checkpoint)
            assert stable == {'changes': [], 'next_changes': checkpoint, 'has_more': False}
            async with session_factory()() as db:
                assert await db.scalar(select(func.count()).select_from(MessageReaction).where(MessageReaction.room_id == room_id)) == 1
                assert await db.scalar(select(func.count()).select_from(GameEvent).where(GameEvent.room_id == room_id,
                    GameEvent.kind == 'chat.reaction_changed')) == 3
    asyncio.run(run())


def test_read_permissions_mute_pause_and_postgame_retry():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, _, outside = clients
            own = find(await catalog(a, base), 'moderator_dm')
            ack = await post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='Private source'))
            async with session_factory()() as db:
                source_id = await db.scalar(select(PrivateMessage.id).where(PrivateMessage.event_id == UUID(ack['event_id'])))
            source = 'private_message:' + str(source_id)
            assert (await react(a, base, own, source)).status_code == 409  # read first
            assert (await react(b, base, own, source)).status_code == 404
            assert (await react(outside, base, own, source)).status_code == 403
            assert (await react(a, base, find(await catalog(a, base), 'common'), source)).status_code == 404
            assert (await read(a, base, own, source)).status_code == 204
            request_id = str(uuid4())
            assert (await react(a, base, own, source, request_id=request_id)).status_code == 200
            response = await host.patch(base + f'/chat/players/{ids[1]}/mute', json=command(muted=True, reason='Reaction gate'), headers={'Origin': ORIGIN})
            assert response.status_code == 200
            assert (await react(a, base, own, source, present=False)).status_code == 403
            response = await host.patch(base + f'/chat/players/{ids[1]}/mute', json=command(muted=False, reason='Restore'), headers={'Origin': ORIGIN})
            assert response.status_code == 200
            await post(host, base + '/game/pause', {'reason': 'Reaction pause'})
            assert (await react(a, base, own, source, present=False)).status_code == 403
            await post(host, base + '/game/resume', {})
            await post(host, base + f'/game/players/{ids[1]}/actions', command(action='kill'))
            # Existing policy permits dead players to reply to an opened GM DM.
            assert (await react(a, base, own, source, present=False)).status_code == 200
            assert (await react(a, base, own, source)).status_code == 200
            common = find(await catalog(a, base), 'common')
            public = (await addressed(host, base, common, command(text='Dead public gate'))).json()
            await read(a, base, common, 'chat:' + public['id'])
            assert (await react(a, base, common, 'chat:' + public['id'])).status_code == 403
            await post(host, base + f'/game/players/{ids[1]}/actions', command(action='revive'))
            await post(host, base + '/game/finish', {'result_label': 'Done', 'result_reason': 'Reaction history'})
            assert (await react(a, base, own, source, request_id=request_id)).status_code == 200  # committed ACK
            assert (await react(a, base, own, source, present=False)).status_code == 403
            item = (await fetch(b, v2(base) + '/' + own['id'] + '/timeline'))['items'][0]
            assert not item['actions']['react'] and item['reactions'][0]['count'] == 1
            history = (await a.get(base + '/transcript', params={'category': 'chat'})).json()['events']
            assert any(event['kind'] == 'chat.reaction_decided' for event in history)
            assert any(event['kind'] == 'chat.reaction_changed' for event in history)
    asyncio.run(run())


def test_old_team_reaction_delivered_only_to_original_readers():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, *players, _ = clients
            actors = dict(zip(ids[1:4], players))
            assignments = (await host.get(base + '/roles/assignments')).json()
            wolves = [row['user_id'] for row in assignments if row['role']['team_chat_label']]
            village = next(row['user_id'] for row in assignments if not row['role']['team_chat_label'])
            a, b, c = actors[wolves[0]], actors[wolves[1]], actors[village]
            team = find(await catalog(host, base), 'team')
            assert not team['capabilities']['react']
            await post(host, base + '/game/advance', {'expected_phase_type': 'day', 'expected_round_number': 0})
            item = (await addressed(host, base, team, command(text='Old team message'))).json()
            source = 'chat:' + item['id']
            assert (await read(a, base, team, source)).status_code == 204
            await post(host, base + f'/game/players/{wolves[1]}/actions', command(action='change_team', text='New team'))
            await post(host, base + f'/game/players/{village}/actions', command(action='change_team', text='Wolf team'))
            before_b, before_c = await sync(b, base), await sync(c, base)
            assert (await react(c, base, team, source)).status_code == 404
            assert (await react(b, base, team, source)).status_code == 403
            assert (await react(a, base, team, source, emoji='🐺')).status_code == 200
            delta_b = await sync(b, base, before_b['next_cursor'])
            assert len(delta_b['deliveries']) == 1 and delta_b['deliveries'][0]['channel_ids'] == [team['id']]
            assert not (await sync(c, base, before_c['next_cursor']))['deliveries']
            old = (await fetch(b, v2(base) + '/' + team['id'] + '/timeline'))['items'][0]
            assert old['reactions'] == [{'emoji': '🐺', 'count': 1, 'mine': False}]
            assert not (await fetch(c, v2(base) + '/' + team['id'] + '/timeline'))['items']
    asyncio.run(run())


def test_caps_and_source_unique_constraint(monkeypatch):
    monkeypatch.setattr(reactions, 'BURST_LIMIT', 100)
    monkeypatch.setattr(reactions, 'MINUTE_LIMIT', 100)
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, c, _ = clients
            channel = find(await catalog(a, base), 'common')
            ack = (await addressed(host, base, channel, command(text='Bounded reactions'))).json()
            source = 'chat:' + ack['id'];emojis = sorted(reactions.ALLOWED_REACTIONS)
            for client in (a, b, c): await read(client, base, channel, source)
            for client, choices in ((a, emojis[:8]), (b, emojis[8:16]), (c, emojis[16:20])):
                for emoji in choices: assert (await react(client, base, channel, source, emoji=emoji)).status_code == 200
            assert (await react(a, base, channel, source, emoji=emojis[20])).status_code == 409
            assert (await react(c, base, channel, source, emoji=emojis[20])).status_code == 409
            assert (await react(c, base, channel, source, emoji=emojis[0])).status_code == 200
            assert (await react(a, base, channel, source, emoji=emojis[1], present=False)).status_code == 200
            assert (await react(c, base, channel, source, emoji=emojis[20])).status_code == 200
    asyncio.run(run())


def test_rate_idempotency_and_invalid_input(monkeypatch):
    monkeypatch.setattr(reactions, 'BURST_LIMIT', 2)
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, *_ = clients
            channel = find(await catalog(a, base), 'common')
            ack = (await addressed(host, base, channel, command(text='Rate source'))).json()
            source = 'chat:' + ack['id']
            for client in (a, b): await read(client, base, channel, source)
            request_id = str(uuid4())
            assert (await react(a, base, channel, source, request_id=request_id)).status_code == 200
            assert (await react(a, base, channel, source, present=False)).status_code == 200
            assert (await react(a, base, channel, source, request_id=request_id)).status_code == 200
            response = await react(a, base, channel, source)
            assert response.status_code == 429 and int(response.headers['Retry-After']) >= 1
            assert (await react(b, base, channel, source, request_id=request_id)).status_code == 409
            assert (await react(a, base, channel, source, emoji='🐺', request_id=request_id)).status_code == 409
            assert (await react(b, base, channel, source, emoji='<script>')).status_code == 422
            response = await b.put(path(base, channel, 'chat', ack['id']), json={**command(emoji='🐺'), 'user_id': ids[1]}, headers={'Origin': ORIGIN})
            assert response.status_code == 422
    asyncio.run(run())


def test_rate_uses_processing_clock_after_a_queue_wait(monkeypatch):
    from app.auth import now_utc
    monkeypatch.setattr(reactions, 'BURST_LIMIT', 1)
    # Model a decision processed 20 seconds after the DB transaction's timestamp.
    monkeypatch.setattr(reactions, 'now_utc', lambda: now_utc() + timedelta(seconds=20))
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            channel = find(await catalog(a, base), 'common')
            ack = (await addressed(host, base, channel, command(text='Queued reaction'))).json()
            source = 'chat:' + ack['id'];await read(a, base, channel, source)
            assert (await react(a, base, channel, source)).status_code == 200
            assert (await react(a, base, channel, source, present=False)).status_code == 429
    asyncio.run(run())


def test_private_source_collision_and_private_activity_stability():
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, *_ = clients
            dm = find(await catalog(host, base), 'moderator_dm', participant_user_id=ids[1])
            private = await post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='One-way source'))
            chat = (await addressed(host, base, dm, command(text='Two-way source'))).json()
            async with session_factory()() as db:
                source_id = await db.scalar(select(PrivateMessage.id).where(PrivateMessage.event_id == UUID(private['event_id'])))
                await db.execute(update(ChatMessage).where(ChatMessage.id == UUID(chat['id'])).values(id=source_id));await db.commit()
            before_b = await sync(b, base)
            general = find(await catalog(b, base), 'common')
            general_snapshot = await fetch(b, v2(base) + '/' + general['id'] + '/timeline')
            for kind, emoji in [('chat', '🐺'), ('private_message', '👨‍👩‍👧‍👦')]:
                assert (await read(a, base, dm, kind + ':' + str(source_id))).status_code == 204
                assert (await react(a, base, dm, kind + ':' + str(source_id), emoji=emoji)).status_code == 200
            page = await fetch(a, v2(base) + '/' + dm['id'] + '/timeline')
            assert [item['reactions'][0]['emoji'] for item in page['items']] == ['👨‍👩‍👧‍👦', '🐺']
            assert await sync(b, base, before_b['next_cursor']) == {'deliveries': [], 'next_cursor': before_b['next_cursor'], 'has_more': False}
            stable = await fetch(b, v2(base) + '/' + general['id'] + '/changes', cursor=general_snapshot['next_changes'])
            assert not stable['changes'] and stable['next_changes'] == general_snapshot['next_changes']
    asyncio.run(run())


def test_reaction_event_outbox_and_state_rollback_together(monkeypatch):
    from app.db import events
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            channel = find(await catalog(a, base), 'common')
            ack = (await addressed(host, base, channel, command(text='Atomic source'))).json();source = 'chat:' + ack['id']
            await read(a, base, channel, source)
            snapshot = await sync(a, base)
            async with session_factory()() as db:
                version = (await db.get(Room, room_id)).version
                sequence = (await db.get(ChatMessage, UUID(ack['id']))).updated_sequence
                count = await db.scalar(select(func.count()).select_from(Outbox).join(GameEvent, GameEvent.id == Outbox.event_id).where(GameEvent.room_id == room_id))
            original = events.record_deliveries
            async def broken(db, room, event):
                await original(db, room, event)
                if event.kind == 'chat.reaction_changed': raise RuntimeError('Injected reaction failure')
            request_id = str(uuid4())
            with monkeypatch.context() as patch:
                patch.setattr(events, 'record_deliveries', broken)
                with pytest.raises(RuntimeError, match='Injected reaction failure'):
                    await react(a, base, channel, source, request_id=request_id)
            async with session_factory()() as db:
                assert (await db.get(Room, room_id)).version == version
                assert (await db.get(ChatMessage, UUID(ack['id']))).updated_sequence == sequence
                assert await db.scalar(select(func.count()).select_from(MessageReaction).where(MessageReaction.room_id == room_id)) == 0
                assert await db.scalar(select(GameEvent.id).where(GameEvent.room_id == room_id, GameEvent.client_request_id == UUID(request_id))) is None
                assert await db.scalar(select(func.count()).select_from(Outbox).join(GameEvent, GameEvent.id == Outbox.event_id).where(GameEvent.room_id == room_id)) == count
            assert not (await sync(a, base, snapshot['next_cursor']))['deliveries']
            assert (await react(a, base, channel, source, request_id=request_id)).status_code == 200
    asyncio.run(run())


@pytest.mark.parametrize('action', ['finish', 'kick'])
def test_pending_reaction_rechecks_rights_after_room_lock(monkeypatch, action):
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, *_ = clients
            channel = find(await catalog(a, base), 'common')
            ack = (await addressed(host, base, channel, command(text='Lock race'))).json();source = 'chat:' + ack['id']
            await read(a, base, channel, source)
            entered = asyncio.Event();original = reactions.require_room_access
            async def signal(*args, **kwargs):
                result = await original(*args, **kwargs);entered.set();return result
            monkeypatch.setattr(reactions, 'require_room_access', signal)
            async with session_factory()() as db:
                room = await db.scalar(select(Room).where(Room.id == room_id).with_for_update())
                pending = asyncio.create_task(react(a, base, channel, source))
                await asyncio.wait_for(entered.wait(), timeout=2)
                if action == 'finish':
                    room.status = 'finished'
                    await append_event(db, room_id, UUID(ids[0]), 'game.finished', 'room', {})
                else:
                    from app.auth import now_utc
                    member = await db.get(RoomMember, (room_id, UUID(ids[1])))
                    member.left_at = now_utc();member.seat_number = None
                    await append_event(db, room_id, UUID(ids[0]), 'member.kicked', 'room', {'request': {'user_id': ids[1]}})
                await db.commit()
            assert (await asyncio.wait_for(pending, timeout=3)).status_code == 403
            async with session_factory()() as db:
                assert await db.scalar(select(func.count()).select_from(MessageReaction).where(MessageReaction.room_id == room_id)) == 0
    asyncio.run(run())
