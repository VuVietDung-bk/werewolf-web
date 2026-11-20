"""UX3 unified DM, old team history and postgame channels in a real browser."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/ux-3'
API, WEB = 'http://127.0.0.1:8026', 'http://127.0.0.1:4176'


def main():
    db_url = os.environ.get('DATABASE_URL', '')
    parsed = urlsplit(db_url)
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != 55432 or parsed.path != '/ma_soi_ux2':
        raise SystemExit('Use only disposable local ma_soi_ux2 at port 55432')
    OUT.joinpath('screenshots').mkdir(exist_ok=True, parents=True)
    actors = [httpx.Client(base_url=API, trust_env=False, timeout=15) for _ in range(3)]
    host, first, second = actors
    ids, checks, errors = [], [], []
    room_id = None

    def post(client, path, body=None):
        result = client.post(path, json=body, headers={'Origin': WEB})
        assert result.status_code in (200, 201), (path, result.status_code, result.text[:300])
        return result.json()

    def command(**kw):
        return {'client_request_id': str(uuid4()), **kw}

    try:
        for client, name in zip(actors, ['Quản trò DM', 'An DM', 'Bình DM']):
            ids.append(post(client, '/api/auth/guest', {'display_name': name})['id'])
        room = post(host, '/api/rooms', {'name': 'Làng thử DM', 'seat_limit': 2})
        room_id = room['id']
        base = '/api/rooms/' + room_id
        for client in actors[1:]:
            post(client, '/api/rooms/join', {'code': room['code'], 'accept_postgame_transcript': True})
        post(host, base + '/roles', {'name': 'Sói bị động', 'description': 'Chỉ trả lời sau khi quản trò mở hội thoại.',
            'faction_label': 'Sói', 'seer_result': 'Sói', 'team_chat_label': 'Bầy sói', 'role_kind': 'passive',
            'quantity': 2, 'host_contact': {'reply_allowed': True}})
        assert host.patch(base + '/settings', json={'name': 'Làng thử DM', 'seat_limit': 2, 'registration_open': False}, headers={'Origin': WEB}).status_code == 200
        post(host, base + '/roles/distribute-random')
        post(host, base + '/game/start')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('UX2_CHROMIUM', '/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'), args=['--no-sandbox', '--disable-dev-shm-usage'])
            contexts, legacy_reads = [], []

            def page_for(client, route):
                context = browser.new_context(viewport={'width': 1440, 'height': 900})
                contexts.append(context)
                context.add_cookies([{'name': 'ma_soi_session', 'value': client.cookies['ma_soi_session'], 'url': WEB, 'httpOnly': True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room'," + json.dumps(room_id) + ')')
                page = context.new_page()
                page.set_default_timeout(10000)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: legacy_reads.append(req.url) if req.method == 'GET' and req.url.endswith('/game/private-messages') else None)
                page.goto(WEB + '/' + route)
                page.locator('.u3-timeline').wait_for()
                return page

            hp = page_for(host, '#chat/dm/' + ids[1])
            ap = page_for(first, '#chat/dm')
            bp = page_for(second, '#chat/dm')
            ap.get_by_text('Role hoặc pha hiện tại chưa cho phép bạn nhắn quản trò. Bạn vẫn đọc được phản hồi.', exact=True).wait_for()
            assert ap.locator('#chat-text').count() == 0
            sent = post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='Tin từ Bàn quản trò'))
            for page in (hp, ap):
                page.get_by_text('Tin từ Bàn quản trò', exact=True).wait_for()
                assert page.locator('.u3-message').count() == 1
            ap.locator('#chat-text').fill('Người chơi phản hồi')
            ap.locator('#chat-text').press('Enter')
            hp.get_by_text('Người chơi phản hồi', exact=True).wait_for()
            hp.locator('#chat-text').fill('Quản trò trả lời từ Chat')
            hp.locator('#chat-text').press('Enter')
            ap.get_by_text('Quản trò trả lời từ Chat', exact=True).wait_for()
            assert bp.locator('.u3-message').count() == 0
            assert bp.locator('#chat-text').count() == 0
            assert ap.locator('.room-sidebar').get_by_role('button', name='Bình DM', exact=True).count() == 0
            checks.append({'case': 'one-way-message-opens-passive-reply-both-sides-share-timeline-no-other-player', 'result': 'pass'})
            # Seed enough sources to require a single interleaved page; no bulk read endpoint.
            expected = [m['event_id'] for m in host.get(base + '/chat/timeline', params={'channel': 'host', 'participant_user_id': ids[1]}).json()['messages']]
            for index in range(35):
                if index % 10 == 9:
                    item = post(host, base + '/chat/messages', command(channel='host', participant_user_id=ids[1], text=f'Chat xen kẽ {index:02d}'))
                else:
                    item = post(host, base + f'/game/players/{ids[1]}/private-messages', command(text=f'Tin từ bảng {index:02d}'))
                expected.append(item['event_id'])
            hp.reload()
            expect(hp.locator('.u3-message')).to_have_count(30)
            hp.get_by_role('button', name='Xem tin cũ hơn', exact=True).click()
            expect(hp.locator('.u3-message')).to_have_count(38)
            assert len(set(hp.locator('.u3-message').evaluate_all('(rows)=>rows.map(row=>row.dataset.itemKey)'))) == 38
            ap.reload()
            expect(ap.locator('.u3-message')).to_have_count(30)
            ap.get_by_role('button', name='Xem tin cũ hơn', exact=True).click()
            expect(ap.locator('.u3-message')).to_have_count(38)
            assert ap.locator('.u3-message').evaluate_all('(rows)=>rows.map(r=>r.dataset.itemKey)') == hp.locator('.u3-message').evaluate_all('(rows)=>rows.map(r=>r.dataset.itemKey)')
            assert not legacy_reads, legacy_reads
            checks.append({'case': 'one-paginated-source-for-both-perspectives-no-duplicate-no-legacy-inbox-read', 'result': 'pass'})
            # Host edits the one-way source using the same per-message menu; live player update.
            row = hp.locator('.u3-message').filter(has_text='Tin từ Bàn quản trò')
            row.hover()
            row.get_by_role('button', name='Thao tác tin của Quản trò', exact=True).click()
            hp.get_by_role('menuitem', name='Sửa', exact=True).click()
            hp.get_by_label('Chỉnh tin nhắn', exact=True).fill('Tin từ bảng đã chỉnh')
            hp.locator('.u3-edit').get_by_role('button', name='Lưu', exact=True).click()
            ap.locator('.u3-message-content > p').filter(has_text='Tin từ bảng đã chỉnh').wait_for()
            row = hp.locator('.u3-message').filter(has_text='Tin từ bảng đã chỉnh')
            row.hover()
            row.get_by_role('button', name='Thao tác tin của Quản trò', exact=True).click()
            hp.get_by_role('menuitem', name='Xóa', exact=True).click()
            hp.locator('.confirmation-dialog[open]').get_by_role('button', name='Xóa tin', exact=True).click()
            expect(ap.locator('.u3-message-content > p').filter(has_text='Tin từ bảng đã chỉnh')).to_have_count(0)
            expect(hp.locator('.u3-message')).to_have_count(37)
            expect(ap.locator('.u3-message')).to_have_count(37)
            assert bp.locator('.u3-message').count() == 0
            checks.append({'case': 'host-menu-edit-delete-private-source-live-player-and-no-leak', 'result': 'pass'})
            hp.screenshot(path=str(OUT / 'screenshots/unified-dm-host.png'))
            ap.screenshot(path=str(OUT / 'screenshots/unified-dm-player.png'))
            # A only-one-way message must badge the DM even while general is open.
            ap.evaluate("location.hash='#chat/general'")
            post(host, base + f'/game/players/{ids[1]}/private-messages', command(text='Tin mới tạo badge DM'))
            ap.locator('.room-sidebar').get_by_role('button', name='Quản trò').get_by_label('Có tin mới').wait_for()
            ap.locator('.room-sidebar').get_by_role('button', name='Quản trò').click()
            ap.get_by_text('Tin mới tạo badge DM', exact=True).wait_for()
            expect(ap.locator('.room-sidebar').get_by_role('button', name='Quản trò').get_by_label('Có tin mới')).to_have_count(0, timeout=8000)
            checks.append({'case': 'one-way-private-message-shares-dm-unread-and-read-boundary', 'result': 'pass'})
            post(host, base + '/game/advance', {'expected_phase_type': 'day', 'expected_round_number': 0})
            post(first, base + '/chat/messages', command(channel='team', text='Lịch sử bầy sói trước đổi đội'))
            post(host, base + f'/game/players/{ids[2]}/actions', command(action='change_team', text='Đội mới'))
            post(first, base + '/chat/messages', command(channel='team', text='Tin sói sau đổi đội'))
            bp.evaluate("location.hash='#chat/team/B%E1%BA%A7y%20s%C3%B3i'")
            bp.get_by_text('Lịch sử bầy sói trước đổi đội', exact=True).wait_for()
            assert 'Tin sói sau đổi đội' not in bp.locator('.u3-message-list').inner_text()
            assert bp.locator('#chat-text').count() == 0
            checks.append({'case': 'former-team-channel-retains-only-delivered-history-readonly', 'result': 'pass'})
            post(host, base + f'/players/{ids[1]}/kick', command(reason='Kiểm tra mở lại hội thoại'))
            hp.reload()
            hp.locator('.room-sidebar').get_by_role('button', name='An DM · đã rời', exact=False).wait_for()
            hp.get_by_text('Người chơi đã rời phòng · Hội thoại chỉ đọc.', exact=True).wait_for()
            assert hp.locator('#chat-text').count() == 0
            expect(hp.locator('.u3-message')).to_have_count(30)
            checks.append({'case': 'host-can-reopen-departed-dm-history-cannot-send', 'result': 'pass'})
            post(host, base + '/game/finish', {'result_label': 'Xong lượt DM', 'result_reason': 'Kiểm tra transcript'})
            bp.reload()
            bp.locator('.room-sidebar').get_by_role('button', name='An DM · đã rời', exact=False).click()
            expect(bp.locator('.u3-message')).to_have_count(30)
            bp.get_by_role('button', name='Xem tin cũ hơn', exact=True).click()
            expect(bp.locator('.u3-message')).to_have_count(39)
            assert bp.locator('.u3-message').filter(has_text='Tin nhắn đã xóa').count() == 1
            deleted = bp.locator('.u3-message').filter(has_text='Tin nhắn đã xóa')
            deleted.locator('summary').click()
            assert deleted.locator('.u3-history li').count() == 3
            assert 'Tin từ Bàn quản trò' in deleted.inner_text()
            assert 'Tin từ bảng đã chỉnh' in deleted.inner_text()
            assert bp.locator('#chat-text').count() == 0
            bp.locator('.room-sidebar').get_by_role('button', name='Bầy sói').click()
            bp.locator('.u3-message-content > p').filter(has_text='Tin sói sau đổi đội').wait_for()
            checks.append({'case': 'postgame-other-player-opens-departed-dm-full-revisions-and-expanded-team', 'result': 'pass'})
            bp.screenshot(path=str(OUT / 'screenshots/unified-dm-postgame.png'))
            assert not errors, errors
            for context in contexts:
                context.close()
            browser.close()
        (OUT / 'dm-browser-results.json').write_text(json.dumps({'scope': 'UX3 second slice: real API/Chromium/disposable PostgreSQL14', 'checks': checks, 'page_errors': errors}, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'dm_checks_passed': len(checks), 'page_errors': errors}), flush=True)
    finally:
        with psycopg.connect(db_url.replace('postgresql+psycopg://', 'postgresql://', 1)) as db:
            if room_id:
                db.execute('DELETE FROM rooms WHERE id=%s', (room_id,))
            for user_id in ids:
                db.execute('DELETE FROM users WHERE id=%s', (user_id,))
        for client in actors:
            client.close()


if __name__ == '__main__':
    main()
