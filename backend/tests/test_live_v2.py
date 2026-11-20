"""Opaque WS/HTTP replay, idle revocation, scoped epochs and queued frame rechecks."""
import asyncio
import json
from urllib.parse import urlencode
from uuid import UUID

import pytest

from app import live, live_v2
from app.auth import COOKIE_NAME
from test_live import ASGISocket
from test_chat_timeline import match, post, command, ORIGIN
from test_channels import addressed, catalog, find
from test_room_delivery import sync, sync_url, assert_safe


class Socket(ASGISocket):
    def __init__(self, base, client, cursor=None, origin=ORIGIN, query=None):
        super().__init__(base.replace('/api/rooms/', '/api/v2/rooms/') + '/live',
            client.cookies.get(COOKIE_NAME), origin=origin)
        self.scope['query_string'] = urlencode(query if query is not None else ({'cursor': cursor} if cursor is not None else {})).encode()

    async def frame(self):
        frame = await asyncio.wait_for(self.output.get(), timeout=2)
        if frame['type'] == 'websocket.close':
            return frame
        data = json.loads(frame['text'])
        assert_safe(data)
        if 'cursor' in data:
            assert isinstance(data['cursor'], str) and len(data['cursor']) == 114
        return data


@pytest.fixture(autouse=True)
def faster_feed(monkeypatch):
    monkeypatch.setattr(live, 'POLL_INTERVAL_SECONDS', .02)


def test_v2_only_own_deliveries_and_http_ws_reconnect_same_checkpoint(monkeypatch):
    monkeypatch.setattr(live_v2, 'BATCH_SIZE', 2)
    async def run():
        async with match() as (clients, ids, base, _room_id):
            host, a, b, *_ = clients
            initial = await sync(a, base)
            socket = Socket(base, a, initial['next_cursor'])
            try:
                assert (await socket.connect())['type'] == 'websocket.accept'
                assert (await socket.frame()) == {'type': 'ready', 'cursor': initial['next_cursor']}
                await post(host, base + f'/game/players/{ids[2]}/private-messages', command(text='B private only'))
                await post(b, base + '/chat/messages', command(channel='host', text='B private reply'))
                await asyncio.sleep(.1)
                assert socket.output.empty()
                token = initial['next_cursor']
                await socket.close()
                for index in range(5):
                    await post(host, base + f'/game/players/{ids[1]}/private-messages', command(text=f'Offline item {index}'))
                # HTTP can deliver the first page; WS resumes the same private feed.
                page = await sync(a, base, token, limit=2)
                assert len(page['deliveries']) == 2 and page['has_more']
                socket = Socket(base, a, page['next_cursor'])
                assert (await socket.connect())['type'] == 'websocket.accept'
                await socket.frame()
                got = []
                while len(got) < 3:
                    frame = await socket.frame()
                    assert frame['type'] == 'invalidation' and len(frame['deliveries']) <= 2
                    got.extend(frame['deliveries']); token = frame['cursor']
                assert len({item['id'] for item in got + page['deliveries']}) == 5
                assert not (await sync(a, base, token))['deliveries']
            finally:
                await socket.close()
            assert not live_v2.hub.feeds
    asyncio.run(run())


def test_v2_scope_errors_and_client_commands():
    async def run():
        async with match() as (clients, _ids, base, _room_id):
            _, a, b, _, outside = clients
            token = (await sync(a, base))['next_cursor']
            for client, origin, query, code in [(a, None, {}, 4403), (outside, ORIGIN, {}, 4403),
                    (a, ORIGIN, {'after_sequence': '1'}, 4400)]:
                socket = Socket(base, client, origin=origin, query=query)
                try:
                    assert (await socket.connect()) == {'type': 'websocket.close', 'code': code, 'reason': ''}
                finally:
                    await socket.close()
            for client, candidate in [(a, token[:-1]+'!'), (b, token), (a, 'x'*10000)]:
                socket = Socket(base, client, candidate)
                try:
                    assert (await socket.connect())['type'] == 'websocket.accept'
                    assert await socket.frame() == {'type': 'resync_required'}
                    assert (await socket.frame())['code'] == 4409
                finally:
                    await socket.close()
            socket = Socket(base, a)
            try:
                await socket.connect();await socket.frame()
                await socket.input.put({'type': 'websocket.receive', 'text': '{"command":"kill"}'})
                assert (await socket.frame())['code'] == 1003
            finally:
                await socket.close()
    asyncio.run(run())


def test_idle_logout_closes_without_new_room_events():
    async def run():
        async with match() as (clients, _ids, base, _room_id):
            a = clients[1]
            socket = Socket(base, a)
            try:
                await socket.connect();await socket.frame()
                assert (await a.post('/api/auth/logout', headers={'Origin': ORIGIN})).status_code == 204
                assert (await socket.frame())['code'] == 4401
            finally:
                await socket.close()
    asyncio.run(run())


def test_slow_consumer_is_bounded_and_does_not_block_other_socket(monkeypatch):
    monkeypatch.setattr(live_v2, 'SEND_TIMEOUT_SECONDS', .04)
    async def run():
        async with match() as (clients, ids, base, room_id):
            host, a, b, *_ = clients
            first, second = Socket(base, a), Socket(base, b)
            try:
                await first.connect();await first.frame();await second.connect();await second.frame()
                feed = live_v2.hub.feeds[room_id]
                connection = next(c for c in feed.connections if c.user_id == UUID(ids[1]))
                original = connection.websocket.send_json
                async def blocked(frame):
                    if frame.get('type') == 'invalidation':
                        await asyncio.Event().wait()
                    await original(frame)
                monkeypatch.setattr(connection.websocket, 'send_json', blocked)
                await post(host, base + '/chat/messages', command(channel='common', text='Other consumer stays live'))
                assert (await second.frame())['type'] == 'invalidation'
                assert (await first.frame())['code'] == 1013
            finally:
                await first.close();await second.close()
    asyncio.run(run())


def test_policy_epoch_only_resyncs_affected_actor():
    async def run():
        async with match() as (clients, ids, base, _room_id):
            host, a, b, *_ = clients
            first, second = Socket(base, a), Socket(base, b)
            try:
                await first.connect();await first.frame();await second.connect();second_ready = await second.frame()
                await post(host, base + f'/game/players/{ids[1]}/actions', command(action='change_team', text='New private team'))
                assert await first.frame() == {'type': 'resync_required'}
                assert (await first.frame())['code'] == 4409
                await asyncio.sleep(.1)
                assert second.output.empty()
                assert not (await sync(b, base, second_ready['cursor']))['deliveries']
                await post(host, base + '/game/finish', {'result_label': 'Done', 'result_reason': 'Expanded access'})
                assert await second.frame() == {'type': 'resync_required'}
                assert (await second.frame())['code'] == 4409
            finally:
                await first.close();await second.close()
    asyncio.run(run())


@pytest.mark.parametrize('revoke', ['kick', 'team'])
def test_pending_frame_is_rechecked_after_loading_before_send(monkeypatch, revoke):
    async def run():
        async with match() as (clients, ids, base, _room_id):
            host, a, *_ = clients
            socket = Socket(base, a)
            loaded, release = asyncio.Event(), asyncio.Event()
            original = live_v2.DeliveryFeed.load_pending
            async def pause(feed, connections):
                rows = await original(feed, connections)
                if any(rows.values()):
                    loaded.set();await release.wait()
                return rows
            try:
                await socket.connect();await socket.frame()
                with monkeypatch.context() as patch:
                    patch.setattr(live_v2.DeliveryFeed, 'load_pending', pause)
                    await post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='Queued private frame'))
                    await asyncio.wait_for(loaded.wait(), timeout=2)
                    if revoke == 'kick':
                        await post(host, base + f'/players/{ids[1]}/kick', command(reason='Queued revoke'))
                    else:
                        await post(host, base + f'/game/players/{ids[1]}/actions', command(action='change_team', text='Changed before send'))
                    release.set()
                    frame = await socket.frame()
                    if revoke == 'team':
                        assert frame == {'type': 'resync_required'};frame = await socket.frame()
                    assert frame['code'] == (4403 if revoke == 'kick' else 4409)
            finally:
                release.set();await socket.close()
    asyncio.run(run())
