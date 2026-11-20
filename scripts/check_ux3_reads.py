"""Read checkpoints in real Chromium: viewport, hidden/modal, retry and two sessions."""
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
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=55432 or parsed.path!='/ma_soi_ux2':
        raise SystemExit('Use only disposable local ma_soi_ux2 at port 55432')
    actors=[httpx.Client(base_url=API,trust_env=False,timeout=15) for _ in range(3)]
    host,a,b=actors;ids=[];room_id=None;checks=[];errors=[]
    def post(client,path,**body):
        response=client.post(path,json=body,headers={'Origin':WEB})
        assert response.status_code in (200,201),(path,response.status_code,response.text[:250])
        return response.json()
    def command(client,path,**body):return post(client,path,client_request_id=str(uuid4()),**body)
    try:
        for client,name in zip(actors,['Host read','An read','Bình read']):ids.append(post(client,'/api/auth/guest',display_name=name)['id'])
        room=post(host,'/api/rooms',name='Làng đọc tin',seat_limit=2);room_id=room['id'];base='/api/rooms/'+room_id
        for client in (a,b):post(client,'/api/rooms/join',code=room['code'],accept_postgame_transcript=True)
        post(host,base+'/roles',name='Role kiểm tra đọc',description='Dùng chat để thử read state.',faction_label='Làng',seer_result='Làng',quantity=2,
             role_kind='active',request_phases=['day','night'],host_contact={'can_initiate':True,'initiate_phases':['day','night'],'reply_allowed':True})
        response=host.patch(base+'/settings',json={'name':room['name'],'seat_limit':2,'registration_open':False},headers={'Origin':WEB});assert response.status_code==200
        post(host,base+'/roles/distribute-random');post(host,base+'/game/start')
        for i in range(40):command(host,base+f'/game/players/{ids[1]}/private-messages',text=f'Lịch sử {i:02d}\n'+'Nội dung lịch sử để cuộn. '*8)
        catalog_path=base.replace('/api/rooms/','/api/v2/rooms/')+'/channels'
        dm=next(item for item in a.get(catalog_path).json()['channels'] if item['kind']=='moderator_dm')
        def unread():return next(item for item in a.get(catalog_path).json()['channels'] if item['id']==dm['id'])['unread']
        def send(text):
            command(host,base+f'/game/players/{ids[1]}/private-messages',text=text)
            response=a.get(catalog_path+'/'+dm['id']+'/timeline',params={'limit':1});assert response.status_code==200
            return response.json()['items'][-1]['id']
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome',args=['--no-sandbox','--disable-dev-shm-usage'])
            posts=[]
            def page_for(route):
                context=browser.new_context(viewport={'width':1280,'height':800})
                context.add_cookies([{'name':'ma_soi_session','value':a.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+')')
                page=context.new_page();page.set_default_timeout(12000)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda request:posts.append(request.post_data_json['visible_item_id'])
                    if request.method=='POST' and request.url.endswith('/read') else None)
                page.goto(WEB+'/'+route);page.locator('.room-live-state[data-state=live]').wait_for()
                return page
            ap=page_for('#chat/dm')
            def until(predicate,timeout=10000):
                elapsed=0
                while not predicate():
                    if elapsed>=timeout:raise AssertionError('Read state did not converge')
                    ap.wait_for_timeout(100);elapsed+=100
            def bottom():
                ap.locator('.u3-timeline').evaluate('(root)=>{root.scrollTop=root.scrollHeight;root.dispatchEvent(new Event("scroll"))}')
            def top():
                ap.locator('.u3-timeline').evaluate('(root)=>{root.scrollTop=0;root.dispatchEvent(new Event("scroll"))}')
            def badge(page):return page.locator('.room-sidebar').get_by_role('button',name=re.compile('Quản trò')).get_by_label('Có tin mới')
            until(lambda:not unread())
            assert posts and all(item.startswith(('chat:','private_message:')) for item in posts)
            checks.append('initial-bottom-viewport-acknowledges-delivered-history')
            peer=page_for('#chat/general')
            top();ap.wait_for_timeout(250)
            key=send('Tin chưa đọc nằm dưới viewport')
            ap.get_by_text('Tin chưa đọc nằm dưới viewport',exact=True).wait_for(state='attached')
            ap.wait_for_timeout(1200)
            assert unread() and key not in posts
            expect(badge(ap)).to_have_count(1)
            expect(badge(peer)).to_have_count(1,timeout=8000)
            checks.append('loaded-offscreen-item-remains-unread-on-server-and-two-sessions')
            bottom();until(lambda:not unread())
            expect(badge(peer)).to_have_count(0,timeout=8000)
            checks.append('viewport-read-persists-and-clears-other-session-badge')
            peer.evaluate('sessionStorage.clear()');peer.reload();peer.locator('.room-live-state[data-state=live]').wait_for()
            expect(badge(peer)).to_have_count(0)
            common=next(item for item in a.get(catalog_path).json()['channels'] if item['kind']=='common')
            wrong_scope=[]
            ap.on('request',lambda request:wrong_scope.append(request.url)
                if request.method=='POST' and request.url.endswith('/'+common['id']+'/read') else None)
            send('Tin mới trong lúc đổi kênh')
            ap.get_by_text('Tin mới trong lúc đổi kênh',exact=True).wait_for(state='attached')
            for _ in range(3):
                ap.locator('.room-sidebar button .room-entry-name').get_by_text('general',exact=True).click()
                ap.locator('.room-sidebar').get_by_role('button',name=re.compile('Quản trò')).click()
            until(lambda:not unread())
            assert not wrong_scope  # general is empty; an old DM observer must not ACK it.
            checks.append('clearing-browser-storage-and-reload-keeps-server-read-state')

            ap.get_by_role('button',name='Menu phòng Làng đọc tin',exact=True).click()
            key=send('Tin sau popup không tự coi là đã đọc')
            ap.wait_for_timeout(1200);assert unread() and key not in posts
            ap.keyboard.press('Escape');until(lambda:not unread())
            checks.append('open-modal-blocks-ack-until-chat-is-visible-again')

            ap.evaluate('''window.__readHidden=true;Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>window.__readHidden?'hidden':'visible'});
                document.dispatchEvent(new Event('visibilitychange'));''')
            key=send('Tin trong tab mô phỏng đang ẩn')
            ap.wait_for_timeout(1200);assert unread() and key not in posts
            ap.evaluate("window.__readHidden=false;document.dispatchEvent(new Event('visibilitychange'))")
            until(lambda:not unread())
            checks.append('simulated-hidden-document-blocks-ack-and-recovers-on-visibilitychange')

            held=[];target={'key':None}
            def delay(route):
                if route.request.post_data_json['visible_item_id']==target['key'] and not held:held.append(route)
                else:route.continue_()
            ap.route('**/channels/*/read',delay)
            target['key']=send('Visible before delayed ACK')
            until(lambda:bool(held))
            top();ap.wait_for_timeout(250)
            newer=send('Tin đến sau mốc đang xác nhận')
            held[0].continue_();ap.wait_for_timeout(1200)
            assert unread() and newer not in posts
            ap.unroute('**/channels/*/read',delay)
            bottom();until(lambda:not unread())
            checks.append('new-arrival-during-delayed-ack-stays-unread-until-actually-seen')

            failed=[];target={'key':None}
            def fail_once(route):
                if route.request.post_data_json['visible_item_id']==target['key'] and not failed:
                    failed.append(True);route.fulfill(status=503,json={'detail':'Read ACK thử lỗi mạng'})
                else:route.continue_()
            ap.route('**/channels/*/read',fail_once)
            target['key']=send('ACK thất bại được tự thử lại')
            until(lambda:bool(failed));assert unread()
            until(lambda:not unread(),timeout=10000)
            assert posts.count(target['key'])>=2
            ap.unroute('**/channels/*/read',fail_once)
            checks.append('failed-read-ack-retries-without-losing-unread-state')

            before=a.get(catalog_path).json()
            command(host,base+f'/game/players/{ids[2]}/private-messages',text='Private to Bình only')
            command(b,base+'/chat/messages',channel='host',text='Bình private reply')
            command(host,base+f'/game/players/{ids[2]}/actions',action='note',text='Private note')
            ap.wait_for_timeout(500)
            assert a.get(catalog_path).json()==before
            checks.append('unrelated-private-activity-does-not-change-catalog-or-unread')

            top();ap.wait_for_timeout(200)
            key=send('Tin sẽ xóa trước khi được thấy')
            until(unread)
            kind,source_id=key.split(':')
            command(host,catalog_path+f"/{dm['id']}/items/{kind}/{source_id}/revisions",action='delete')
            until(lambda:not unread())
            assert key not in posts
            checks.append('deleting-the-only-unread-item-clears-badge-without-forged-read')
            assert not errors,errors
            browser.close()
        result={'scope':'server chat read checkpoints: real Chromium/HTTP/WS/PostgreSQL14',
            'checks':[{'case':item,'result':'pass'} for item in checks],'page_errors':errors}
        (ROOT/'docs/ux-3/read-browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'read_checks_passed':len(checks),'page_errors':errors}),flush=True)
    finally:
        if room_id:
            with psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')) as db:
                db.execute('DELETE FROM rooms WHERE id=%s',(room_id,))
                for uid in ids:db.execute('DELETE FROM users WHERE id=%s',(uid,))
        for client in actors:client.close()


if __name__=='__main__':main()
