"""Verify the UX2 application on a disposable local database; no production data."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
import psycopg
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/ux-2'
API='http://127.0.0.1:8026'
WEB='http://127.0.0.1:4176'


def main():
    db_url=os.environ.get('DATABASE_URL','')
    parsed=urlsplit(db_url)
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=55432 or parsed.path!='/ma_soi_ux2':
        raise SystemExit('DATABASE_URL must use local port 55432 and disposable ma_soi_ux2 database')
    OUT.joinpath('screenshots').mkdir(parents=True,exist_ok=True)
    actors=[httpx.Client(base_url=API,trust_env=False,timeout=15) for _ in range(4)]
    host,*players=actors
    ids=[];room_id=None;next_room_id=None;checks=[];page_errors=[]
    def post(client,path,body=None,status=200):
        r=client.post(path,json=body,headers={'Origin':WEB})
        assert r.status_code==status,(path,r.status_code,r.text[:300])
        return r.json() if r.content else None
    def command(**kw):return {'client_request_id':str(uuid4()),**kw}
    try:
        for client,name in zip(actors,['Quản trò UX2','An UX2','Bình UX2','Chi UX2']):
            ids.append(post(client,'/api/auth/guest',{'display_name':name})['id'])
        room=post(host,'/api/rooms',{'name':'Làng UX2','seat_limit':3},201);room_id=room['id'];base=f'/api/rooms/{room_id}'
        for client in players:post(client,'/api/rooms/join',{'code':room['code'],'accept_postgame_transcript':True})
        for name,qty,team in [('Sói UX2',2,'Bầy sói'),('Tiên Tri UX2',1,None)]:
            post(host,base+'/roles',{'name':name,'description':'Mô tả kín và điều kiện mẫu của '+name,
                'faction_label':'Sói' if team else 'Làng','seer_result':'Sói' if team else 'Làng',
                'team_chat_label':team,'role_kind':'active','request_phases':['day','night'],'quantity':qty,
                'host_contact':{'can_initiate':True,'initiate_phases':['day','night'],'reply_allowed':True}},201)
        post(players[0],base+'/chat/messages',command(channel='common',text='Tin ở sảnh trước khi phát role.'))
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('UX2_CHROMIUM','/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'),args=['--no-sandbox','--disable-dev-shm-usage'])
            contexts=[]
            def new_page(client):
                context=browser.new_context(viewport={'width':1440,'height':900});contexts.append(context)
                context.add_cookies([{'name':'ma_soi_session','value':client.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+")")
                page=context.new_page();page.set_default_timeout(10000)
                page.on('pageerror',lambda e:page_errors.append(str(e)))
                return page
            def go(page,route):
                page.goto(WEB+'/'+route);page.locator('.room-topbar nav').wait_for()
            def tabs(page):assert page.locator('.room-topbar nav button').all_text_contents()==['Chat','Role','Quản lý']
            hp=new_page(host);go(hp,'#chat/general');tabs(hp)
            hp.get_by_text('Tin ở sảnh trước khi phát role.',exact=True).wait_for()
            hp.locator('#chat-text').fill('Quản trò gửi tin ở sảnh.')
            hp.get_by_role('button',name='Gửi tin',exact=True).click()
            hp.get_by_text('Quản trò gửi tin ở sảnh.',exact=True).wait_for()
            hp.get_by_role('button',name='Quản lý',exact=True).click()
            hp.get_by_role('button',name='Thông tin phòng',exact=True).click()
            hp.locator('#settings-name').fill('Làng UX2')
            hp.get_by_label('Tiếp tục nhận người chơi').uncheck()
            hp.get_by_role('button',name='Lưu cài đặt',exact=True).click()
            hp.get_by_role('button',name='Role',exact=True).click()
            hp.get_by_role('button',name='Phát role',exact=True).click()
            hp.get_by_role('button',name='Phát role ngẫu nhiên',exact=True).click()
            hp.get_by_role('button',name='Bắt đầu ngày 0',exact=True).wait_for()
            hp.get_by_role('button',name='Bắt đầu ngày 0',exact=True).click()
            hp.locator('.confirmation-dialog[open]').get_by_role('button',name='Bắt đầu Ngày 0',exact=True).click()
            hp.locator('.room-phase').filter(has_text='Ngày 0').wait_for()
            checks.append({'case':'lobby-chat-settings-distribute-start-via-ui','result':'pass'})
            assignments=host.get(base+'/roles/assignments').json()
            by_id=dict(zip(ids[1:],players))
            wolf_id=next(x['user_id'] for x in assignments if x['role']['team_chat_label'])
            villager_id=next(x['user_id'] for x in assignments if not x['role']['team_chat_label'])
            wp=new_page(by_id[wolf_id]);vp=new_page(by_id[villager_id])
            go(wp,'#chat/general');go(vp,'#chat/general')
            hp.get_by_role('button',name='Chat',exact=True).click()
            hp.locator('.room-sidebar').get_by_role('button',name='Bầy sói').wait_for()
            wp.locator('.room-sidebar').get_by_role('button',name='Bầy sói').wait_for()
            assert vp.locator('.room-sidebar').get_by_role('button',name='Bầy sói').count()==0
            assert 'Sói UX2' not in vp.locator('.room-member-sidebar').inner_text()
            checks.append({'case':'host-wolf-villager-channel-permissions','result':'pass'})
            for w,h in [(1440,900),(1024,768),(390,844),(320,740)]:
                hp.set_viewport_size({'width':w,'height':h})
                data=hp.evaluate('''() => ({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,bodyOverflow:getComputedStyle(document.body).overflow,composerBottom:document.querySelector('.chat-send')?.getBoundingClientRect().bottom})''')
                assert data['scrollWidth']<=w and data['scrollHeight']<=h,data
                assert data['composerBottom']<=h,data
                tabs(hp)
                if w<760:
                    trigger=hp.get_by_role('button',name='Mở danh sách kênh hoặc tác vụ')
                    trigger.click();hp.locator('.room-drawer[open]').wait_for()
                    for _ in range(12):
                        hp.keyboard.press('Tab')
                        assert hp.evaluate("document.querySelector('.room-drawer[open]').contains(document.activeElement)")
                    hp.keyboard.press('Escape')
                    assert hp.evaluate("document.activeElement.getAttribute('aria-label')")=='Mở danh sách kênh hoặc tác vụ'
                if w<1200:
                    hp.get_by_role('button',name='Mở danh sách người chơi').click();hp.locator('.room-drawer[open]').wait_for();hp.keyboard.press('Escape')
                hp.screenshot(path=str(OUT/'screenshots'/f'host-chat-{w}.png'))
                checks.append({'case':f'layout-{w}','result':'pass','metrics':data})
            for w,h in [(390,480),(844,390)]:
                hp.set_viewport_size({'width':w,'height':h})
                hp.locator('#chat-text').focus()
                assert hp.locator('.chat-send').bounding_box()['y']+hp.locator('.chat-send').bounding_box()['height']<=h
                assert hp.evaluate('document.documentElement.scrollWidth<=innerWidth')
            checks.append({'case':'focus-trap-short-keyboard-viewport-and-landscape','result':'pass'})
            hp.set_viewport_size({'width':1440,'height':900})
            go(hp,'#dead');hp.wait_for_url('**/#chat/announcements')
            go(hp,'#vote');hp.wait_for_url('**/#chat/vote');tabs(hp)
            hp.reload();hp.locator('.room-channel-header h1').filter(has_text='Vote').wait_for()
            hp.get_by_role('button',name='Role',exact=True).click()
            hp.get_by_role('button',name='Chat',exact=True).click()
            hp.go_back();hp.locator('.room-topbar nav [aria-current="page"]').filter(has_text='Role').wait_for()
            hp.go_forward();hp.locator('.room-topbar nav [aria-current="page"]').filter(has_text='Chat').wait_for()
            go(vp,'#management/players');vp.wait_for_url('**/#management/room')
            assert vp.locator('.host-board').count()==0
            go(vp,'#role/edit');vp.wait_for_url('**/#role/catalog')
            checks.append({'case':'legacy-links-reload-back-forward-host-guards','result':'pass'})
            post(host,base+'/game/advance',{'expected_phase_type':'day','expected_round_number':0})
            post(by_id[wolf_id],base+'/chat/messages',command(channel='team',text='Tin riêng của đội Sói UX2.'))
            go(wp,'#chat/team/B%E1%BA%A7y%20s%C3%B3i')
            wp.get_by_text('Tin riêng của đội Sói UX2.',exact=True).wait_for()
            assert by_id[villager_id].get(base+'/chat/messages',params={'channel':'team','team_label':'Bầy sói'}).status_code==426
            go(vp,'#chat/general');assert 'Tin riêng của đội Sói UX2.' not in vp.locator('.chat-message-list').inner_text()
            wp.screenshot(path=str(OUT/'screenshots'/'wolf-team.png'));vp.screenshot(path=str(OUT/'screenshots'/'villager-general.png'))
            post(host,base+'/chat/messages',command(channel='host',participant_user_id=villager_id,text='Chat riêng chỉ cho người Làng.'))
            go(vp,'#chat/dm');vp.get_by_text('Chat riêng chỉ cho người Làng.',exact=True).wait_for()
            assert by_id[wolf_id].get(base+'/chat/messages',params={'channel':'host'}).status_code==426
            vp.get_by_role('button',name='Yêu cầu dùng role…').click()
            vp.locator('dialog[open] #u3-request-title').wait_for()
            vp.keyboard.press('Escape')
            def delay_send(route):
                if route.request.method!='POST':
                    route.continue_();return
                response=route.fetch()
                time.sleep(0.4)
                route.fulfill(response=response)
            vp.route('**/channels/*/messages',delay_send)
            vp.locator('#chat-text').fill('Tin DM gửi trước khi chuyển kênh.')
            vp.evaluate("""() => {
                document.querySelector('.chat-send').requestSubmit();
                setTimeout(()=>{location.hash='#chat/general'},50);
            }""")
            vp.wait_for_url('**/#chat/general')
            vp.locator('.chat-message-list').wait_for()
            vp.wait_for_timeout(600)
            assert 'Tin DM gửi trước khi chuyển kênh.' not in vp.locator('.chat-message-list').inner_text()
            vp.unroute('**/channels/*/messages',delay_send)
            checks.append({'case':'delayed-private-send-cannot-enter-general-view','result':'pass'})
            checks.append({'case':'live-team-filter-and-private-request-access','result':'pass'})
            go(hp,'#chat/general')
            post(host,base+f'/game/players/{villager_id}/actions',command(action='kill',reason='Kiểm tra UX2 giữ kín'))
            hp.reload();hp.locator('.room-member-sidebar').wait_for()
            assert 'Đã công bố chết — 0' in hp.locator('.room-member-sidebar').text_content()
            checks.append({'case':'hidden-death-does-not-change-public-roster','result':'pass'})
            go(wp,'#chat/general')
            wp.locator('.room-sidebar').get_by_role('button',name='Bầy sói').wait_for()
            roster_before=wp.locator('.room-member-sidebar').text_content()
            channels_before=wp.locator('.room-sidebar .room-entry-name').all_text_contents()
            wolf_role_id=next(item['role']['id'] for item in assignments if item['role']['team_chat_label'])
            post(host,base+f'/game/players/{villager_id}/actions',command(action='change_role',role_id=wolf_role_id))
            post(host,base+f'/game/players/{villager_id}/actions',command(action='note',text='Ghi chú kín UX2 chỉ host thấy.'))
            wp.reload();wp.locator('.room-sidebar').get_by_role('button',name='Bầy sói').wait_for()
            assert wp.locator('.room-member-sidebar').text_content()==roster_before
            assert wp.locator('.room-sidebar .room-entry-name').all_text_contents()==channels_before
            assert 'Ghi chú kín UX2 chỉ host thấy.' not in wp.locator('.room-layout').inner_text()
            checks.append({'case':'private-role-and-note-do-not-change-other-player-shell','result':'pass'})

            post(host,base+'/game/finish',{'result_label':'Kết thúc thử UX2','result_reason':'Kiểm tra shell sau trận'})
            go(hp,'#chat/general');assert hp.locator('#chat-text').count()==0
            go(hp,'#management/game');hp.get_by_text('Kết thúc thử UX2',exact=True).wait_for()
            hp.screenshot(path=str(OUT/'screenshots'/'finished.png'))
            go(hp,'#management/history');hp.locator('.transcript-screen').wait_for();tabs(hp)
            checks.append({'case':'finished-readonly-result-transcript','result':'pass'})
            # Reopen the same lobby via the UI and exercise the draft route with a real player's turn.
            go(hp,'#management/game')
            hp.get_by_role('button',name='Bắt đầu trận mới trong sảnh',exact=True).click()
            hp.locator('.confirmation-dialog[open]').get_by_role('button',name='Bắt đầu ván mới',exact=True).click()
            hp.wait_for_url('**/#chat/general');tabs(hp)
            next_room_id=host.get(base).json()['next_room_id']
            assert next_room_id and next_room_id!=room_id
            next_base=f'/api/rooms/{next_room_id}'
            post(host,next_base+'/roles',{'name':'Thợ săn UX2','description':'Role mẫu thứ ba cho lượt draft.',
                'faction_label':'Làng','seer_result':'Làng','role_kind':'passive','quantity':0},201)
            r=host.patch(next_base+'/settings',json={'name':'Làng UX2','seat_limit':3,'registration_open':False},headers={'Origin':WEB})
            assert r.status_code==200,r.text
            candidates=[item['id'] for item in host.get(next_base+'/role-catalog').json()['roles']]
            started=post(host,next_base+'/roles/draft/start',{'candidate_role_ids':candidates})
            turn=next(item for item in started['host_turns'] if item['turn_index']==started['current_turn'])
            dp=new_page(by_id[turn['user_id']])
            dp.context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(next_room_id)+")")
            go(dp,'#role/mine')
            dp.get_by_role('button',name='Chọn role này',exact=True).first.wait_for()
            dp.get_by_role('button',name='Chọn role này',exact=True).first.click()
            dp.get_by_text('Đang chờ đến lượt.',exact=False).wait_for()
            tabs(dp);dp.screenshot(path=str(OUT/'screenshots'/'player-draft.png'))
            checks.append({'case':'next-lobby-and-player-draft-route','result':'pass'})
            assert not page_errors,page_errors
            for c in contexts:c.close()
            browser.close()
        result={'scope':'UX2 real frontend and API; disposable PostgreSQL 14; current legacy inner features', 'checks':checks,'page_errors':page_errors}
        (OUT/'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'checks_passed':len(checks),'page_errors':page_errors},ensure_ascii=False))
    finally:
        # Only this script's dummy room/accounts in the explicit test database.
        with psycopg.connect(db_url.replace('postgresql+psycopg://','postgresql://',1)) as db:
            if room_id:db.execute('UPDATE rooms SET next_room_id=NULL WHERE id=%s',(room_id,))
            if next_room_id:db.execute('DELETE FROM rooms WHERE id=%s',(next_room_id,))
            if room_id:db.execute('DELETE FROM rooms WHERE id=%s',(room_id,))
            for user_id in ids:db.execute('DELETE FROM users WHERE id=%s',(user_id,))
        for client in actors:client.close()

if __name__=='__main__':main()
