"""Real Chromium/HTTP/WS: one room provider, recovery, private scopes and cleanup."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
API, WEB = 'http://127.0.0.1:8026', 'http://127.0.0.1:4176'


def main():
    parsed = urlsplit(os.environ.get('DATABASE_URL', ''))
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != 55432 or parsed.path != '/ma_soi_ux2':
        raise SystemExit('Use disposable local ma_soi_ux2 at port 55432 only')
    actors = [httpx.Client(base_url=API, trust_env=False, timeout=15) for _ in range(3)]
    host, a, b = actors
    room_id, ids = None, []
    checks, errors = [], []

    def post(client, path, **body):
        response = client.post(path, json=body, headers={'Origin': WEB})
        assert response.status_code in (200, 201), (path, response.status_code, response.text[:250])
        return response.json()

    def command(client, path, **body):
        return post(client, path, client_request_id=str(uuid4()), **body)

    try:
        for client, name in zip(actors, ['Host realtime', 'An realtime', 'Bình realtime']):
            ids.append(post(client, '/api/auth/guest', display_name=name)['id'])
        room = post(host, '/api/rooms', name='Làng realtime v2', seat_limit=2)
        room_id = room['id'];base = '/api/rooms/' + room_id
        for client in (a, b):
            post(client, '/api/rooms/join', code=room['code'], accept_postgame_transcript=True)
        roles = []
        for name in ('Tiên Tri realtime', 'Sói realtime'):
            roles.append(post(host, base + '/roles', name=name, description='Role cho kiểm tra realtime',
                faction_label='Làng' if name.startswith('Tiên') else 'Sói', seer_result='Làng', quantity=1,
                role_kind='active', request_phases=['day', 'night'],
                host_contact={'can_initiate': True, 'initiate_phases': ['day', 'night'], 'reply_allowed': True}))
        response = host.patch(base + '/settings', json={'name': room['name'], 'seat_limit': 2, 'registration_open': False}, headers={'Origin': WEB})
        assert response.status_code == 200
        post(host, base + '/roles/distribute-random');post(host, base + '/game/start')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path='/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome',
                args=['--no-sandbox', '--disable-dev-shm-usage'])
            frames, urls = {}, {}

            def page_for(client):
                context = browser.new_context(viewport={'width': 1440, 'height': 900})
                context.add_cookies([{'name': 'ma_soi_session', 'value': client.cookies['ma_soi_session'], 'url': WEB, 'httpOnly': True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room'," + json.dumps(room_id) + ')')
                context.add_init_script('''window.__roomSockets=[];window.__blockRoomLive=false;
                    const Native=window.WebSocket;
                    window.WebSocket=class extends Native {constructor(...args){
                      if(window.__blockRoomLive)throw new Error('Simulated WS unavailable');
                      super(...args);window.__roomSockets.push(this);
                    }};''')
                page = context.new_page();page.set_default_timeout(12000)
                frames[page], urls[page] = [], []
                def socket_open(socket):
                    urls[page].append(socket.url)
                    def receive(payload):
                        try: frames[page].append(json.loads(payload))
                        except (ValueError, TypeError): pass
                    socket.on('framereceived', receive)
                page.on('websocket', socket_open)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(WEB + '/#chat/general')
                page.locator('.room-live-state[data-state=live]').wait_for()
                return page

            ap, hp = page_for(a), page_for(host)
            ap.wait_for_function('window.__roomSockets.filter(s=>s.readyState===1).length===1')
            initial_count = ap.evaluate('window.__roomSockets.length')
            nav = ap.get_by_role('navigation', name='Điều hướng phòng')
            for tab in ('Role', 'Quản lý', 'Chat', 'Role', 'Chat'):
                nav.get_by_role('button', name=tab, exact=True).click()
            ap.locator('#chat-text').wait_for()
            assert ap.evaluate('window.__roomSockets.filter(s=>s.readyState===1).length') == 1
            assert ap.evaluate('window.__roomSockets.length') == initial_count
            assert all('/api/v2/rooms/' in url and '/live' in url and 'after_sequence' not in url for url in urls[ap] + urls[hp])
            checks.append('one-v2-socket-survives-tab-navigation-no-legacy-websocket')

            key = f'ma-soi-room-live-v2:{room_id}:{ids[1]}'
            ap.wait_for_timeout(200)
            cursor = ap.evaluate('(key)=>sessionStorage.getItem(key)', key)
            frame_count = len(frames[ap])
            command(host, base + f'/game/players/{ids[2]}/private-messages', text='Private to Bình')
            command(b, base + '/chat/messages', channel='host', text='Bình private reply')
            command(host, base + f'/game/players/{ids[2]}/actions', action='note', text='Hidden note for Bình')
            ap.wait_for_timeout(700)
            assert len(frames[ap]) == frame_count
            assert ap.evaluate('(key)=>sessionStorage.getItem(key)', key) == cursor
            checks.append('unrelated-private-dm-note-do-not-change-ws-or-cursor')

            command(host, base + '/chat/messages', channel='common', text='Tin trực tiếp 🌙')
            ap.get_by_text('Tin trực tiếp 🌙', exact=True).wait_for(timeout=3000)
            checks.append('general-chat-arrives-through-room-invalidation')

            ap.locator('#chat-text').fill('Nháp giữ qua mất mạng')
            ap.evaluate('window.__blockRoomLive=true;window.__roomSockets.filter(s=>s.readyState<2).forEach(s=>s.close())')
            command(host, base + '/chat/messages', channel='common', text='HTTP recovery một')
            command(host, base + '/chat/messages', channel='common', text='HTTP recovery hai')
            ap.get_by_text('HTTP recovery hai', exact=True).wait_for(timeout=9000)
            expect(ap.locator('#chat-text')).to_have_value('Nháp giữ qua mất mạng')
            ap.evaluate('window.__blockRoomLive=false')
            ap.locator('.room-live-state[data-state=live]').wait_for(timeout=15000)
            assert ap.get_by_text('HTTP recovery một', exact=True).count() == 1
            assert ap.get_by_text('HTTP recovery hai', exact=True).count() == 1
            checks.append('ws-down-http-drains-and-reconnects-without-duplicates-or-lost-draft')

            ap.evaluate('(key)=>sessionStorage.setItem(key,"broken-cursor")', key)
            ap.reload();ap.locator('.room-live-state[data-state=live]').wait_for(timeout=15000)
            expect(ap.locator('#chat-text')).to_have_value('Nháp giữ qua mất mạng')
            assert any(frame.get('type') == 'resync_required' for frame in frames[ap])
            checks.append('bad-live-token-resyncs-snapshots-and-retains-draft')

            nav = ap.get_by_role('navigation', name='Điều hướng phòng')
            nav.get_by_role('button', name='Role', exact=True).click()
            current = ap.locator('.my-role h3').inner_text()
            replacement = next(role for role in roles if role['name'] != current)
            before_version = a.get(base).json()['version']
            command(host, base + f'/game/players/{ids[1]}/actions', action='change_role', role_id=replacement['id'])
            ap.locator('.my-role h3').get_by_text(replacement['name'], exact=True).wait_for(timeout=3000)
            assert a.get(base).json()['version'] == before_version
            checks.append('private-role-update-refreshes-current-tab-without-public-room-version-change')

            nav.get_by_role('button', name='Chat', exact=True).click()
            ap.locator('#chat-text').wait_for()
            expect(ap.locator('#chat-text')).to_have_value('Nháp giữ qua mất mạng')
            command(host, base + f'/game/players/{ids[1]}/actions', action='change_team', text='Đội riêng realtime')
            ap.locator('.room-sidebar').get_by_text('Đội riêng realtime', exact=True).wait_for(timeout=3000)
            expect(ap.locator('#chat-text')).to_have_value('Nháp giữ qua mất mạng')
            checks.append('own-team-policy-resync-refreshes-catalog-without-losing-general-draft')

            hp.get_by_role('navigation', name='Điều hướng phòng').get_by_role('button', name='Quản lý', exact=True).click()
            hp.locator('.room-sidebar').get_by_text('Yêu cầu role', exact=True).click()
            request = command(a, base + '/role-requests', text='Xin xác nhận qua realtime')
            hp.locator('.role-request-list').get_by_text('Xin xác nhận qua realtime', exact=True).wait_for(timeout=3000)
            ap.locator('.room-sidebar').get_by_text('Quản trò', exact=True).click()
            command(host, base + f"/role-requests/{request['id']}/review", status='accepted', reply_text='Phản hồi realtime cho An')
            ap.locator('.u3-request-reply').filter(has_text='Phản hồi realtime cho An').wait_for(timeout=3000)
            checks.append('request-queue-and-player-dm-share-realtime-review-scope')

            hp.get_by_role('navigation', name='Điều hướng phòng').get_by_role('button', name='Chat', exact=True).click()
            hp.locator('.room-sidebar').get_by_role('button', name='Vote', exact=True).click()
            reviews = []
            hp.on('request', lambda request: reviews.append(request.url) if '/review' in request.url else None)
            vote = command(host, base + '/votes')
            hp.locator('.vote-panel').wait_for()
            hp.wait_for_timeout(300)
            count = len(reviews)
            command(b, base + f"/votes/{vote['vote_round_id']}/ballots", choice='skip')
            hp.wait_for_timeout(1500)
            assert len(reviews) > count
            ap.locator('.room-sidebar').get_by_text('Thông báo', exact=True).click()
            command(host, base + '/game/events/announcements', text='Thông báo qua provider chung')
            ap.get_by_text('Thông báo qua provider chung', exact=True).wait_for(timeout=3000)
            checks.append('vote-review-and-announcements-refresh-through-shared-scopes')

            hp.locator('.room-sidebar button .room-entry-name').get_by_text('general', exact=True).click()
            hp.locator('#chat-text').wait_for()
            command(host, base + f"/votes/{vote['vote_round_id']}/close")
            command(host, base + f'/players/{ids[1]}/kick', reason='Realtime revoke')
            ap.get_by_text('Bạn không còn quyền vào phòng này.', exact=True).wait_for(timeout=4000)
            assert ap.locator('.room-layout').count() == 0
            assert ap.evaluate('window.__roomSockets.filter(s=>s.readyState===1).length') == 0
            assert ap.evaluate('(key)=>sessionStorage.getItem(key)', key) is None
            checks.append('kick-closes-socket-clears-room-and-scoped-token')

            assert host.post('/api/auth/logout', headers={'Origin': WEB}).status_code == 204
            hp.get_by_text('Phiên đăng nhập đã hết hạn. Vui lòng vào lại.', exact=True).wait_for(timeout=4000)
            assert hp.locator('.room-layout').count() == 0
            checks.append('idle-session-revocation-returns-login-and-cleans-socket')

            forbidden = {'sequence', 'position', 'event_id', 'source_event_id', 'payload', 'kind', 'recipient_ids', 'policy_epoch'}
            def safe(value):
                if isinstance(value, dict):
                    assert not forbidden & value.keys()
                    for child in value.values(): safe(child)
                elif isinstance(value, list):
                    for child in value: safe(child)
            for frame in frames[ap] + frames[hp]:
                safe(frame)
            assert not errors, errors
            browser.close()
        output = {'scope': 'UX3 shared room realtime: real Chromium/HTTP/WS/PostgreSQL14',
            'checks': [{'case': case, 'result': 'pass'} for case in checks], 'page_errors': errors}
        (ROOT / 'docs/ux-3/realtime-browser-results.json').write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'realtime_checks_passed': len(checks), 'page_errors': errors}), flush=True)
    finally:
        if room_id:
            with psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')) as db:
                db.execute('DELETE FROM rooms WHERE id=%s', (room_id,))
                for user_id in ids: db.execute('DELETE FROM users WHERE id=%s', (user_id,))
        for client in actors: client.close()


if __name__ == '__main__':
    main()
