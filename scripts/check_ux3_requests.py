"""Role request cards in the real DM timeline, shared queue, retries and privacy."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
import psycopg
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
API,WEB='http://127.0.0.1:8026','http://127.0.0.1:4176'

def main():
    parsed=urlsplit(os.environ.get('DATABASE_URL',''))
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=55432 or parsed.path!='/ma_soi_ux2':raise SystemExit('Use disposable ma_soi_ux2 at localhost:55432')
    clients=[httpx.Client(base_url=API,trust_env=False,timeout=20) for _ in range(3)]
    host,a,b=clients;ids=[];room_id=None;checks=[];errors=[]
    def post(client,path,**data):
        response=client.post(path,json=data,headers={'Origin':WEB});assert response.status_code in (200,201),(path,response.status_code,response.text[:200]);return response.json()
    def command(client,path,**data):return post(client,path,client_request_id=str(uuid4()),**data)
    try:
        for client,name in zip(clients,['Host yêu cầu','Quản trò','Bình yêu cầu']):ids.append(post(client,'/api/auth/guest',display_name=name)['id'])
        room=post(host,'/api/rooms',name='Làng yêu cầu trong DM',seat_limit=2);room_id=room['id'];base='/api/rooms/'+room_id
        for client in (a,b):post(client,'/api/rooms/join',code=room['code'],accept_postgame_transcript=True)
        active=post(host,base+'/roles',name='Tiên Tri kiểm tra',description='Yêu cầu tự do; quản trò quyết định.',faction_label='Làng',seer_result='Làng',role_kind='active',request_phases=['day'],quantity=2,
            host_contact={'can_initiate':True,'initiate_phases':['day','night'],'reply_allowed':True})
        passive=post(host,base+'/roles',name='Bị động kiểm tra',description='Không tự gửi yêu cầu.',faction_label='Làng',seer_result='Làng',role_kind='passive',quantity=0,host_contact={'reply_allowed':True})
        response=host.patch(base+'/settings',json={'name':room['name'],'seat_limit':2,'registration_open':False},headers={'Origin':WEB});assert response.status_code==200
        post(host,base+'/roles/distribute-random');post(host,base+'/game/start')
        command(host,base+f'/game/players/{ids[1]}/private-messages',text='Thông tin riêng của quản trò')
        cat=base.replace('/api/rooms/','/api/v2/rooms/')+'/channels'
        a_dm=next(item for item in a.get(cat).json()['channels'] if item['kind']=='moderator_dm')
        def unread():return next(item for item in a.get(cat).json()['channels'] if item['id']==a_dm['id'])['unread']
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('UX2_CHROMIUM','/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'),args=['--no-sandbox','--disable-dev-shm-usage'])
            frames=[]
            def page_for(client,hash):
                context=browser.new_context(viewport={'width':1440,'height':900})
                context.add_cookies([{'name':'ma_soi_session','value':client.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+')')
                page=context.new_page();page.set_default_timeout(12000);page.on('pageerror',lambda error:errors.append(str(error)))
                if client is b:
                    def socket_open(socket):
                        def receive(data):
                            if json.loads(data).get('type')=='invalidation':frames.append(data)
                        socket.on('framereceived',receive)
                    page.on('websocket',socket_open)
                page.goto(WEB+'/'+hash);page.locator('.room-live-state[data-state=live]').wait_for();return page
            ap=page_for(a,'#chat/dm');bp=page_for(b,'#chat/general');hp=page_for(host,'#chat/dm/'+ids[1])
            legacy=[]
            ap.on('request',lambda request:legacy.append(request.url) if request.method=='GET' and any(request.url.split('?')[0].endswith(path) for path in ['/chat/messages','/chat/timeline','/game/private-messages','/role-requests']) else None)
            b_catalog=b.get(cat).json()
            def card(page,text):return page.locator('.u3-request').filter(has=page.locator('.u3-message-content > p').filter(has_text=text))
            def send_request(text):
                ap.get_by_role('button',name='Yêu cầu dùng role…',exact=True).click()
                ap.locator('#role-request-text').fill(text)
                ap.locator('dialog[open]').get_by_role('button',name='Gửi yêu cầu',exact=True).click()
            first='Đoán 2 người, xác nhận <b>chỉ là text</b> 🐺'
            send_request(first);card(ap,first).wait_for();card(hp,first).wait_for()
            assert card(ap,first).locator('b,.chat-host-badge,.u3-message-tools').count()==0
            assert bp.get_by_text(first,exact=True).count()==0 and b.get(cat).json()==b_catalog
            assert not frames
            checks.append('request-is-one-private-source-with-safe-unicode-and-no-fake-host-badge')
            queue=host.get(base+'/role-requests').json()['requests'];key=queue[0]['id']
            assert card(ap,first).get_attribute('data-item-key')=='role_request:'+key
            ap.locator('#chat-text').fill('Tin nhắn thường giữ nguyên chat');ap.locator('#chat-text').press('Enter')
            ap.get_by_text('Tin nhắn thường giữ nguyên chat',exact=True).wait_for()
            assert len(host.get(base+'/role-requests').json()['requests'])==1
            checks.append('plain-chat-is-not-a-role-request-and-queue-shares-canonical-id')
            card(hp,first).get_by_role('button',name='Xử lý yêu cầu',exact=True).click()
            dialog=hp.locator('dialog[open]');dialog.get_by_label('Phản hồi cho người gửi').fill('Đang xác nhận thông tin')
            dialog.get_by_role('button',name='Lưu phản hồi',exact=True).click()
            expect(card(ap,first).locator('.u3-request-status')).to_have_text('Đang xét')
            card(hp,first).get_by_role('button',name='Xử lý yêu cầu',exact=True).click()
            hp.locator('dialog[open]').get_by_label('Phản hồi cho người gửi').fill('Kết quả đã xác nhận là 2')
            hp.locator('dialog[open]').get_by_role('button',name='Lưu phản hồi',exact=True).click()
            expect(card(ap,first).locator('.u3-request-status')).to_have_text('Đã chấp nhận')
            expect(card(ap,first).locator('.u3-request-reply')).to_contain_text('Kết quả đã xác nhận là 2')
            assert card(ap,first).count()==1 and card(hp,first).count()==1
            hp.get_by_role('button',name='Quản lý',exact=True).click();hp.locator('.room-sidebar').get_by_text('Yêu cầu role',exact=True).click()
            hp.locator('.role-request-list').get_by_text('Kết quả đã xác nhận là 2').wait_for()
            checks.append('review-dialog-updates-existing-card-and-moderator-queue-without-copy')
            second='Xin rút yêu cầu này'
            send_request(second);card(ap,second).get_by_role('button',name='Rút yêu cầu',exact=True).click()
            ap.locator('.confirmation-dialog[open]').get_by_role('button',name='Rút yêu cầu',exact=True).click()
            expect(card(ap,second).locator('.u3-request-status')).to_have_text('Đã rút')
            hp.locator('.role-request-list').filter(has_text=second).get_by_text('Đã rút',exact=True).wait_for()
            checks.append('withdraw-updates-same-item-and-queue-in-realtime')
            attempts=[]
            def lose_ack(route):
                attempts.append(route.request.post_data_json['client_request_id']);response=route.fetch()
                if len(attempts)==1:route.fulfill(status=503,content_type='application/json',body='{"detail":"Mất ACK yêu cầu giả lập"}')
                else:route.fulfill(response=response)
            ap.route('**/role-requests',lose_ack)
            third='Yêu cầu retry không nhân đôi'
            send_request(third);ap.locator('dialog[open]').get_by_role('alert').filter(has_text='Mất ACK yêu cầu giả lập').wait_for()
            ap.reload();ap.get_by_role('button',name='Yêu cầu dùng role…',exact=True).click();expect(ap.locator('#role-request-text')).to_have_value(third)
            ap.locator('dialog[open]').get_by_role('button',name='Gửi yêu cầu',exact=True).click()
            expect(ap.locator('dialog[open]')).to_have_count(0);card(ap,third).wait_for()
            assert len(attempts)==2 and attempts[0]==attempts[1]
            ap.unroute('**/role-requests',lose_ack)
            requests=host.get(base+'/role-requests').json()['requests'];assert len(requests)==3
            third_id=next(item['id'] for item in requests if item['text']==third)
            checks.append('lost-ack-keeps-draft-and-request-id-across-reload-without-extra-source')
            ap.evaluate("location.hash='#chat/general'");ap.locator('#chat-text').wait_for()
            command(host,base+'/role-requests/'+third_id+'/review',status='in_review',reply_text='Đã nhận yêu cầu thứ ba')
            for _ in range(100):
                if unread():break
                ap.wait_for_timeout(100)
            assert unread()
            ap.evaluate("location.hash='#chat/dm'");card(ap,third).locator('.u3-request-reply').filter(has_text='Đã nhận yêu cầu thứ ba').wait_for()
            for _ in range(100):
                if not unread():break
                ap.wait_for_timeout(100)
            assert not unread()
            ap.get_by_role('button',name='Menu phòng Làng yêu cầu trong DM',exact=True).click()
            command(host,base+'/role-requests/'+third_id+'/review',status='accepted',reply_text='Phản hồi khi modal đang che chat')
            ap.wait_for_timeout(800);assert unread()
            ap.keyboard.press('Escape')
            for _ in range(100):
                if not unread():break
                ap.wait_for_timeout(100)
            assert not unread()
            checks.append('request-review-unread-follows-visible-revision-and-modal-blocks-ack')
            command(host,base+f'/game/players/{ids[1]}/actions',action='change_role',role_id=passive['id'])
            ap.get_by_role('button',name='Yêu cầu dùng role…',exact=True).click()
            expect(ap.locator('#role-request-text')).to_be_disabled()
            expect(ap.locator('dialog[open]')).to_contain_text('Role bị động')
            ap.keyboard.press('Escape');ap.locator('#chat-text').fill('Bị động vẫn trả lời DM đã mở');ap.locator('#chat-text').press('Enter')
            ap.get_by_text('Bị động vẫn trả lời DM đã mở',exact=True).wait_for()
            assert len(host.get(base+'/role-requests').json()['requests'])==3
            checks.append('passive-role-cannot-submit-request-and-ordinary-dm-policy-is-independent')
            command(host,base+f'/game/players/{ids[1]}/actions',action='change_role',role_id=active['id'])
            post(host,base+'/game/advance',expected_phase_type='day',expected_round_number=0)
            ap.get_by_role('button',name='Yêu cầu dùng role…',exact=True).click()
            expect(ap.locator('#role-request-text')).to_be_disabled();expect(ap.locator('dialog[open]')).to_contain_text('Role không được gửi yêu cầu ở pha này')
            ap.keyboard.press('Escape');checks.append('role-request-phase-gate-remains-server-derived')
            assert not legacy,legacy
            for path in ['/chat/messages?channel=common','/chat/timeline?channel=host','/game/private-messages','/role-requests']:
                response=a.get(base+path);assert response.status_code==426
            result=ap.evaluate('''base=>new Promise(resolve=>{const frames=[];const socket=new WebSocket(location.origin.replace('http','ws')+base+'/live?after_sequence=0');
                socket.onmessage=event=>frames.push(event.data);socket.onclose=event=>resolve({code:event.code,frames});})''',base)
            assert result=={'code':4406,'frames':[]},result
            checks.append('player-ui-uses-v2-and-legacy-http-ws-cannot-expose-numeric-metadata')
            post(host,base+'/game/finish',result_label='Hoàn tất UX3',result_reason='Kiểm tra request trong DM')
            ap.get_by_text('Đã kết thúc · Chỉ đọc',exact=True).first.wait_for()
            bp.evaluate("location.hash='#chat/dm/"+ids[1]+"'");expect(bp.locator('.u3-request')).to_have_count(3)
            assert bp.locator('.u3-request').get_by_role('button').count()==0
            assert bp.get_by_role('button',name='Yêu cầu dùng role…',exact=True).count()==0
            checks.append('postgame-expands-request-history-under-consent-and-locks-actions')
            out=ROOT/'docs/ux-3/screenshots';out.mkdir(parents=True,exist_ok=True)
            bp.screenshot(path=str(out/'requests-postgame.png'))
            ap.set_viewport_size({'width':320,'height':480});assert ap.evaluate('document.documentElement.scrollWidth<=innerWidth')
            ap.screenshot(path=str(out/'requests-mobile.png'));checks.append('request-cards-fit-mobile-320px')
            assert not errors,errors;browser.close()
        result={'scope':'UX3 requests and legacy boundaries: Chromium + real API/PostgreSQL14','checks':checks,'page_errors':errors}
        (ROOT/'docs/ux-3/request-browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        if room_id:
            with psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')) as db:
                db.execute('DELETE FROM rooms WHERE id=%s',(room_id,))
                for uid in ids:db.execute('DELETE FROM users WHERE id=%s',(uid,))
        for client in clients:client.close()
if __name__=='__main__':main()
