"""UX3 registry/DM browser checks; --opaque adds v2 cursor and history race cases."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlencode
from uuid import uuid4

import httpx
import psycopg
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/ux-3'
API, WEB = 'http://127.0.0.1:8026', 'http://127.0.0.1:4176'


def main():
    opaque='--opaque' in sys.argv
    db_url = os.environ.get('DATABASE_URL', '')
    parsed = urlsplit(db_url)
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != 55432 or parsed.path != '/ma_soi_ux2':
        raise SystemExit('Use only disposable local ma_soi_ux2 at port 55432')
    OUT.joinpath('screenshots').mkdir(exist_ok=True, parents=True)
    actors = [httpx.Client(base_url=API, trust_env=False, timeout=15) for _ in range(3)]
    host, first, second = actors
    ids, checks, errors = [], [], []
    room_id = None
    v2_payloads,legacy_chat_reads,tokens_seen=[],[],[]

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
            policy_reads = {}

            def page_for(client, route):
                context = browser.new_context(viewport={'width': 1440, 'height': 900})
                contexts.append(context)
                context.add_cookies([{'name': 'ma_soi_session', 'value': client.cookies['ma_soi_session'], 'url': WEB, 'httpOnly': True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room'," + json.dumps(room_id) + ')')
                page = context.new_page()
                page.set_default_timeout(10000)
                policy_reads[page]=[]
                page.on('request',lambda req: policy_reads[page].append(req.url) if req.method=='GET' and req.url.endswith('/chat/context') else None)
                if opaque:
                    def capture_read(route):
                        response=route.fetch()
                        if response.status==200:
                            v2_payloads.append(response.json())
                            for key,value in parse_qs(urlsplit(route.request.url).query).items():
                                if key in ('before','cursor'):tokens_seen.extend(value)
                        route.fulfill(response=response)
                    page.route('**/api/v2/rooms/*/channels/*/timeline*',capture_read)
                    page.route('**/api/v2/rooms/*/channels/*/changes*',capture_read)
                    page.on('request',lambda req:legacy_chat_reads.append(req.url) if req.method=='GET' and any(part in req.url for part in ('/chat/timeline','/chat/messages','/game/private-messages')) else None)
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
            policy_reads[bp].clear()
            bp.wait_for_timeout(12500)
            assert 2<=len(policy_reads[bp])<=3,policy_reads[bp]
            own_id=next(item['id'] for item in first.get(base.replace('/api/rooms/','/api/v2/rooms/')+'/channels').json()['channels'] if item['kind']=='moderator_dm')
            assert own_id in hp.evaluate("performance.getEntriesByType('resource').map(x=>x.name).join(' ')")
            checks.append({'case':'shared-single-channel-policy-poll-and-addressed-send','result':'pass'})

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
            if opaque:
                corrupted=[]
                def corrupt_once(route):
                    if corrupted:route.continue_();return
                    query=parse_qs(urlsplit(route.request.url).query)
                    token=query['cursor'][0]
                    query['cursor']=[token[:30]+('A' if token[30]!='A' else 'B')+token[31:]]
                    response=route.fetch(url=urlsplit(route.request.url)._replace(query=urlencode(query,doseq=True)).geturl())
                    assert response.status==409
                    assert response.json()['detail']['code']=='chat_resync_required'
                    corrupted.append(True)
                    route.fulfill(response=response)
                hp.locator('#chat-text').fill('Nháp giữ qua cursor không hợp lệ')
                hp.route('**/api/v2/rooms/*/channels/*/changes*',corrupt_once)
                expect(hp.locator('.u3-message')).to_have_count(30,timeout=9000)
                assert corrupted
                expect(hp.locator('#chat-text')).to_have_value('Nháp giữ qua cursor không hợp lệ')
                hp.unroute('**/api/v2/rooms/*/channels/*/changes*',corrupt_once)
                hp.get_by_role('button',name='Xem tin cũ hơn',exact=True).click()
                expect(hp.locator('.u3-message')).to_have_count(38)
                hp.locator('#chat-text').fill('')
                checks.append({'case':'tampered-cursor-real-api-resync-retains-draft-and-pagination-recovers','result':'pass'})
                hp.reload()
                hp.locator('#chat-text').wait_for()
                expect(hp.locator('.u3-message')).to_have_count(30)
                bad_before=[]
                def corrupt_history_once(route):
                    if bad_before or 'before=' not in route.request.url:route.fallback();return
                    query=parse_qs(urlsplit(route.request.url).query)
                    token=query['before'][0]
                    query['before']=[token[:30]+('A' if token[30]!='A' else 'B')+token[31:]]
                    response=route.fetch(url=urlsplit(route.request.url)._replace(query=urlencode(query,doseq=True)).geturl())
                    assert response.status==409
                    bad_before.append(True);route.fulfill(response=response)
                hp.route('**/api/v2/rooms/*/channels/*/timeline*',corrupt_history_once)
                hp.locator('#chat-text').fill('Nháp giữ qua lỗi lịch sử cũ')
                with hp.expect_response(lambda response:'/timeline' in response.url and 'before=' not in response.url and response.status==200):
                    with hp.expect_response(lambda response:'/timeline' in response.url and response.status==409):
                        hp.get_by_role('button',name='Xem tin cũ hơn',exact=True).click()
                expect(hp.locator('.u3-message')).to_have_count(30)
                hp.unroute('**/api/v2/rooms/*/channels/*/timeline*',corrupt_history_once)
                assert bad_before
                expect(hp.locator('#chat-text')).to_have_value('Nháp giữ qua lỗi lịch sử cũ')
                hp.get_by_role('button',name='Xem tin cũ hơn',exact=True).click()
                expect(hp.locator('.u3-message')).to_have_count(38)
                hp.locator('#chat-text').fill('')
                checks.append({'case':'invalid-before-token-resnapshots-without-stuck-history-button','result':'pass'})
                hp.reload()
                hp.locator('#chat-text').wait_for()
                expect(hp.locator('.u3-message')).to_have_count(30)
                delayed=[]
                def lag_history(route):
                    if delayed or 'before=' not in route.request.url:route.fallback();return
                    delayed.append(True)
                    response=route.fetch()
                    target=next(item for item in response.json()['items'] if item['text']=='Quản trò trả lời từ Chat')
                    revision_path=base.replace('/api/rooms/','/api/v2/rooms/')+'/channels/'+own_id+'/items/'+target['source']['kind']+'/'+target['source']['id']+'/revisions'
                    post(host,revision_path,command(action='edit',text='Cập nhật trong lúc tải lịch sử'))
                    for _ in range(25):
                        if any(any(change['item']['text']=='Cập nhật trong lúc tải lịch sử' for change in data.get('changes',[])) for data in v2_payloads):break
                        hp.wait_for_timeout(100)
                    assert any(any(change['item']['text']=='Cập nhật trong lúc tải lịch sử' for change in data.get('changes',[])) for data in v2_payloads)
                    route.fulfill(response=response)
                hp.route('**/api/v2/rooms/*/channels/*/timeline*',lag_history)
                hp.get_by_role('button',name='Xem tin cũ hơn',exact=True).click()
                hp.get_by_text('Cập nhật trong lúc tải lịch sử',exact=False).wait_for()
                expect(hp.locator('.u3-message')).to_have_count(38)
                hp.unroute('**/api/v2/rooms/*/channels/*/timeline*',lag_history)
                target=next(item for item in host.get(base.replace('/api/rooms/','/api/v2/rooms/')+'/channels/'+own_id+'/timeline',params={'limit':50}).json()['items'] if item['text']=='Cập nhật trong lúc tải lịch sử')
                post(host,base.replace('/api/rooms/','/api/v2/rooms/')+'/channels/'+own_id+'/items/'+target['source']['kind']+'/'+target['source']['id']+'/revisions',command(action='edit',text='Quản trò trả lời từ Chat'))
                checks.append({'case':'late-history-snapshot-reconciles-revision-for-previously-unloaded-source','result':'pass'})

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
            # Loading/caching history is not a read ACK: actually reveal the row.
            ap.get_by_text('Tin mới tạo badge DM', exact=True).scroll_into_view_if_needed()
            expect(ap.locator('.room-sidebar').get_by_role('button', name='Quản trò').get_by_label('Có tin mới')).to_have_count(0, timeout=8000)
            checks.append({'case': 'one-way-private-message-shares-dm-unread-and-read-boundary', 'result': 'pass'})
            ap.route('**/api/v2/rooms/*/channels',lambda route:route.fulfill(status=503,json={'detail':'Không tải được registry thử'}))
            ap.get_by_role('button',name='Menu phòng Làng thử DM',exact=True).click()
            ap.get_by_role('button',name='Cập nhật phòng',exact=True).click()
            ap.keyboard.press('Escape')
            ap.locator('.chat-panel > .form-message').filter(has_text='Không tải được registry thử').wait_for()
            assert ap.locator('#chat-text').count()==0
            assert ap.locator('.u3-message').count()==0
            ap.unroute('**/api/v2/rooms/*/channels')
            ap.get_by_role('button',name='Menu phòng Làng thử DM',exact=True).click()
            ap.get_by_role('button',name='Cập nhật phòng',exact=True).click()
            ap.keyboard.press('Escape')
            ap.locator('#chat-text').wait_for()
            ap.get_by_text('Tin mới tạo badge DM',exact=True).wait_for()
            checks.append({'case':'permission-service-failure-hides-stale-send-controls-and-recovers','result':'pass'})
            post(host, base + '/game/advance', {'expected_phase_type': 'day', 'expected_round_number': 0})
            post(first, base + '/chat/messages', command(channel='team', text='Lịch sử bầy sói trước đổi đội'))
            bp.evaluate("location.hash='#chat/team/B%E1%BA%A7y%20s%C3%B3i'")
            bp.locator('#chat-text').wait_for()
            stale_catalog=second.get(base.replace('/api/rooms/','/api/v2/rooms/')+'/channels').json()
            stale_context=second.get(base+'/chat/context').json()
            bp.route('**/api/v2/rooms/*/channels',lambda route:route.fulfill(status=200,json=stale_catalog))
            bp.route('**/chat/context',lambda route:route.fulfill(status=200,json=stale_context))
            post(host, base + f'/game/players/{ids[2]}/actions', command(action='change_team', text='Đội mới'))
            bp.locator('#chat-text').fill('Không gửi lạc sang đội mới')
            bp.locator('#chat-text').press('Enter')
            bp.locator('.chat-panel > .form-message').filter(has_text='đội cũ').wait_for()
            second_team=next(item for item in second.get(base.replace('/api/rooms/','/api/v2/rooms/')+'/channels').json()['channels'] if item['kind']=='team' and item['team_label']=='Đội mới')
            assert not any(item['text']=='Không gửi lạc sang đội mới' for item in second.get(base.replace('/api/rooms/','/api/v2/rooms/')+'/channels/'+second_team['id']+'/timeline').json()['items'])
            bp.unroute('**/api/v2/rooms/*/channels')
            bp.unroute('**/chat/context')
            bp.reload()
            checks.append({'case':'stale-team-screen-cannot-send-into-new-team','result':'pass'})

            post(first, base + '/chat/messages', command(channel='team', text='Tin sói sau đổi đội'))
            bp.evaluate("location.hash='#chat/team/B%E1%BA%A7y%20s%C3%B3i'")
            bp.get_by_text('Lịch sử bầy sói trước đổi đội', exact=True).wait_for()
            assert 'Tin sói sau đổi đội' not in bp.locator('.u3-message-list').inner_text()
            assert bp.locator('#chat-text').count() == 0
            checks.append({'case': 'former-team-channel-retains-only-delivered-history-readonly', 'result': 'pass'})
            bp.evaluate("location.hash='#chat/team'")
            bp.locator('.u3-empty').wait_for()
            bp.locator('#chat-text').wait_for()
            expect(bp.locator('.room-channel-header h1')).to_have_text('Đội mới')
            assert 'Lịch sử bầy sói trước đổi đội' not in bp.locator('.u3-message-list').inner_text()
            bp.locator('#chat-text').fill('Tin từ link đội hiện tại')
            bp.locator('#chat-text').press('Enter')
            bp.get_by_text('Tin từ link đội hiện tại',exact=True).wait_for()
            checks.append({'case':'generic-team-bookmark-resolves-current-identity-without-old-team-cache','result':'pass'})

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
            if opaque:
                def no_ordinals(value):
                    if isinstance(value,dict):
                        assert not {'sequence','updated_sequence','event_id','room_cursor','source_event_id','recipient_ids'} & value.keys(),value
                        for child in value.values():no_ordinals(child)
                    elif isinstance(value,list):
                        for child in value:no_ordinals(child)
                assert v2_payloads and tokens_seen
                for data in v2_payloads:no_ordinals(data)
                assert all(len(token)==114 and token.startswith('c1.') for token in tokens_seen)
                assert not legacy_chat_reads,legacy_chat_reads
                checks.append({'case':'browser-uses-only-v2-timeline-with-fixed-length-opaque-cursors-and-no-public-ordinals','result':'pass'})
            assert not errors, errors
            for context in contexts:
                context.close()
            browser.close()
        scope='UX3 opaque HTTP timeline/cursors' if opaque else 'UX3 channel registry'
        (OUT / ('cursor-browser-results.json' if opaque else 'channel-browser-results.json')).write_text(json.dumps({'scope': scope+': real API/Chromium/disposable PostgreSQL14', 'checks': checks, 'page_errors': errors}, ensure_ascii=False, indent=2) + '\n')
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
