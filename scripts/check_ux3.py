"""UX3 timeline/composer behavior on a disposable local database and real API."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/ux-3'
API='http://127.0.0.1:8026'
WEB='http://127.0.0.1:4176'


def main():
    db_url=os.environ.get('DATABASE_URL','')
    parsed=urlsplit(db_url)
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=55432 or parsed.path!='/ma_soi_ux2':
        raise SystemExit('Use only disposable local port 55432/database ma_soi_ux2')
    OUT.joinpath('screenshots').mkdir(parents=True,exist_ok=True)
    actors=[httpx.Client(base_url=API,trust_env=False,timeout=15) for _ in range(3)]
    host,first,second=actors
    ids=[];room_id=None;checks=[];errors=[]
    def post(client,path,body=None,status=200):
        response=client.post(path,json=body,headers={'Origin':WEB})
        assert response.status_code==status,(path,response.status_code,response.text[:300])
        return response.json() if response.content else None
    def command(**kw):return {'client_request_id':str(uuid4()),**kw}
    def sql(query,args):
        with psycopg.connect(db_url.replace('postgresql+psycopg://','postgresql://',1)) as db:
            db.execute(query,args)
    def age():
        # Fixture-only clock manipulation avoids waiting for chat rate limits between cases.
        sql("UPDATE chat_messages SET created_at=created_at-interval '2 minutes' WHERE room_id=%s AND created_at>now()-interval '1 minute'",(room_id,))
    try:
        for client,name in zip(actors,['Quản trò UX3','Người chơi có tên dài để kiểm tra ngắt dòng','Bạn Sói UX3']):
            ids.append(post(client,'/api/auth/guest',{'display_name':name})['id'])
        room=post(host,'/api/rooms',{'name':'Làng UX3','seat_limit':2},201)
        room_id=room['id'];base=f'/api/rooms/{room_id}'
        for client in actors[1:]:
            post(client,'/api/rooms/join',{'code':room['code'],'accept_postgame_transcript':True})
        post(host,base+'/roles',{'name':'Sói UX3','description':'Dùng để thử chat ngày và đêm.',
            'faction_label':'Sói','seer_result':'Sói','team_chat_label':'Bầy sói','role_kind':'active',
            'request_phases':['day','night'],'quantity':2,
            'host_contact':{'can_initiate':True,'initiate_phases':['day','night'],'reply_allowed':True}},201)
        assert host.patch(base+'/settings',json={'name':'Làng UX3','seat_limit':2,'registration_open':False},headers={'Origin':WEB}).status_code==200
        post(host,base+'/roles/distribute-random');post(host,base+'/game/start')
        for i in range(65):
            result=post(first,base+'/chat/messages',command(channel='common',text=f'Lịch sử {i:03d} · dòng thứ nhất\nDòng thứ hai 🐺'))
            sql('UPDATE chat_messages SET created_at=%s WHERE room_id=%s AND event_id=%s',
                (datetime.now(timezone.utc)-timedelta(days=1)+timedelta(seconds=i*2),room_id,result['event_id']))
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('UX2_CHROMIUM','/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'),args=['--no-sandbox','--disable-dev-shm-usage'])
            contexts=[]
            def new_page(client):
                context=browser.new_context(viewport={'width':1440,'height':900});contexts.append(context)
                context.add_cookies([{'name':'ma_soi_session','value':client.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+")")
                page=context.new_page();page.set_default_timeout(10000)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(WEB+'/#chat/general');page.locator('#chat-text').wait_for()
                return page
            page=new_page(host)
            rows=page.locator('.u3-message')
            expect(rows).to_have_count(30)
            assert page.locator('.u3-grouped').count()==29
            assert 'Ngày 0' in page.locator('.u3-marker').inner_text()
            assert page.locator('.u3-avatar').count()==1
            assert page.locator('.u3-message-content > p').first.evaluate('(p)=>getComputedStyle(p).whiteSpace')=='pre-wrap'
            checks.append({'case':'paginated-grouped-rows-day-phase-multiline-long-name','result':'pass'})
            timeline=page.locator('.u3-timeline')
            timeline.evaluate('(root)=>{root.scrollTop=0;root.dispatchEvent(new Event("scroll"))}')
            snapshot="""() => {const root=document.querySelector('.u3-timeline');const top=root.getBoundingClientRect().top;const row=[...root.querySelectorAll('[data-item-key]')].find(r=>r.getBoundingClientRect().bottom>top);return {key:row.dataset.itemKey,offset:row.getBoundingClientRect().top-top,scroll:root.scrollTop}}"""
            before=page.evaluate(snapshot)
            page.get_by_role('button',name='Xem tin cũ hơn',exact=True).click()
            expect(rows).to_have_count(60)
            page.wait_for_timeout(100)
            after=page.evaluate(snapshot)
            original_offset=page.locator('[data-item-key="'+before['key']+'"]').evaluate('(row)=>row.getBoundingClientRect().top-document.querySelector(".u3-timeline").getBoundingClientRect().top')
            assert abs(original_offset-before['offset'])<3,(before,after,original_offset)
            before=after
            result=post(second,base+'/chat/messages',command(channel='common',text='Tin mới khi đang đọc lịch sử'))
            page.get_by_text('Tin mới khi đang đọc lịch sử',exact=True).wait_for()
            page.get_by_role('button',name='1 tin mới · Đến cuối ↓',exact=True).wait_for()
            after=page.evaluate(snapshot)
            assert after['key']==before['key'] and abs(after['offset']-before['offset'])<3,(before,after)
            checks.append({'case':'prepend-anchor-and-incoming-message-do-not-jump-reader','result':'pass'})
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'")
            page.locator('.u3-empty').wait_for()
            page.evaluate("location.hash='#chat/general'")
            expect(rows).to_have_count(61)
            page.wait_for_timeout(150)
            after=page.evaluate(snapshot)
            assert after['key']==before['key'] and abs(after['offset']-before['offset'])<3,(before,after)
            checks.append({'case':'restore-channel-position-with-loaded-history','result':'pass'})
            # Switching away clears the per-view count; verify its next increment and jump button.
            age();post(second,base+'/chat/messages',command(channel='common',text='Tin mới thứ hai'))
            page.get_by_role('button',name='1 tin mới · Đến cuối ↓',exact=True).click()
            assert timeline.evaluate('(r)=>r.scrollHeight-r.scrollTop-r.clientHeight')<2
            post(second,base+'/chat/messages',command(channel='common',text='Tin mới khi đang ở cuối'))
            page.get_by_text('Tin mới khi đang ở cuối',exact=True).wait_for()
            assert timeline.evaluate('(r)=>r.scrollHeight-r.scrollTop-r.clientHeight')<2
            checks.append({'case':'jump-to-latest-and-follow-at-bottom','result':'pass'})
            box=page.locator('#chat-text')
            box.fill('Dòng một');box.press('Shift+Enter');box.type('Dòng hai :wolf:')
            assert '\n' in box.input_value()
            box.press('Enter')
            page.get_by_text('Dòng một\nDòng hai 🐺',exact=True).wait_for()
            expect(box).to_have_value('')
            box.fill('Tiếng Việt đang nhập')
            box.dispatch_event('compositionstart')
            box.press('Enter')
            page.wait_for_timeout(100)
            assert not page.locator('.u3-message-content > p').filter(has_text='Tiếng Việt đang nhập').count()
            box.dispatch_event('compositionend')
            box.fill('Tiếng Việt đã hoàn tất');box.press('Enter')
            page.get_by_text('Tiếng Việt đã hoàn tất',exact=True).wait_for()
            checks.append({'case':'shift-enter-ime-enter-send-and-alias-expansion','result':'pass'})
            box.fill('Nháp chung kín của host')
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'")
            expect(box).to_have_value('')
            box.fill('Nháp DM riêng')
            page.get_by_role('button',name='Role',exact=True).click()
            page.get_by_role('button',name='Chat',exact=True).click()
            expect(box).to_have_value('Nháp chung kín của host')
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'")
            expect(box).to_have_value('Nháp DM riêng')
            page.reload();expect(box).to_have_value('Nháp DM riêng')
            page.evaluate("location.hash='#chat/general'")
            expect(box).to_have_value('Nháp chung kín của host')
            player_page=new_page(first)
            copied=page.evaluate('Object.entries(sessionStorage)')
            player_page.evaluate('(entries)=>entries.forEach(([key,value])=>sessionStorage.setItem(key,value))',copied)
            player_page.reload()
            expect(player_page.locator('#chat-text')).to_have_value('')
            checks.append({'case':'drafts-channel-workspace-reload-user-isolation','result':'pass'})
            box.fill('Chèn: ')
            page.get_by_role('button',name='Chọn emoji',exact=True).click()
            search=page.get_by_role('textbox',name='Tìm emoji')
            expect(search).to_be_focused();search.fill('SÓI');search.press('ArrowDown');page.keyboard.press('Enter')
            expect(box).to_have_value('Chèn: 🐺');expect(box).to_be_focused()
            page.get_by_role('button',name='Chọn emoji',exact=True).click()
            assert page.get_by_role('heading',name='Gần dùng',exact=True).count()==1
            search.fill(':family:');page.get_by_role('button',name='Gia đình',exact=True).click()
            expect(box).to_have_value('Chèn: 🐺👨‍👩‍👧‍👦')
            page.get_by_role('button',name='Chọn emoji',exact=True).click()
            page.screenshot(path=str(OUT/'screenshots'/'emoji-picker.png'))
            for _ in range(60):
                page.keyboard.press('Tab')
                assert page.evaluate("document.querySelector('.u3-emoji-dialog[open]').contains(document.activeElement)")
            page.keyboard.press('Escape');expect(box).to_be_focused()
            checks.append({'case':'emoji-search-vietnamese-alias-family-recent-keyboard-focus','result':'pass'})
            age()
            literal='<img src=x onerror=alert(1)> 👨‍👩‍👧‍👦 :unknown:'
            box.fill(literal);box.press('Enter');page.get_by_text(literal,exact=True).wait_for()
            assert page.locator('.u3-message-content img,.u3-message-content script').count()==0
            checks.append({'case':'html-literal-and-unicode-roundtrip','result':'pass'})
            request_ids=[]
            def lost_ack(route):
                if route.request.method!='POST':route.continue_();return
                request_ids.append(route.request.post_data_json['client_request_id'])
                response=route.fetch()
                if len(request_ids)==1:route.fulfill(status=503,content_type='application/json',body='{"detail":"Mất ACK giả lập"}')
                else:route.fulfill(response=response)
            age();page.route('**/channels/*/messages',lost_ack)
            box.fill('Retry chỉ một tin')
            expect(page.get_by_role('button',name='Gửi tin',exact=True)).to_be_enabled()
            box.press('Enter')
            page.get_by_role('alert').filter(has_text='Mất ACK giả lập').wait_for()
            expect(box).to_have_value('Retry chỉ một tin')
            page.reload()
            expect(box).to_have_value('Retry chỉ một tin')
            box.press('Enter');expect(box).to_have_value('')
            assert len(request_ids)==2 and request_ids[0]==request_ids[1],request_ids
            page.unroute('**/channels/*/messages',lost_ack)
            stored=host.get(base+'/chat/messages',params={'channel':'common'}).json()['messages']
            assert len([m for m in stored if m['text']=='Retry chỉ một tin'])==1
            checks.append({'case':'lost-ack-retains-draft-reload-retry-idempotent','result':'pass'})
            def delayed_ack(route):
                if route.request.method!='POST':route.continue_();return
                response=route.fetch()
                page.wait_for_timeout(300)
                route.fulfill(response=response)
            age();page.route('**/channels/*/messages',delayed_ack)
            box.fill('Tin đang gửi')
            page.evaluate("""() => {document.querySelector('.chat-send').requestSubmit(); setTimeout(()=>{const t=document.querySelector('#chat-text');const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(t,'Nháp mới lúc chờ gửi');t.dispatchEvent(new Event('input',{bubbles:true}));},50)}""")
            page.get_by_text('Tin đang gửi',exact=True).wait_for()
            expect(box).to_have_value('Nháp mới lúc chờ gửi')
            page.unroute('**/channels/*/messages',delayed_ack)
            checks.append({'case':'late-ack-does-not-clear-new-draft','result':'pass'})
            page.screenshot(path=str(OUT/'screenshots'/'chat-general-1440.png'))
            # Source timestamps survive changing the room to night; no current-phase relabeling.
            post(host,base+'/game/advance',{'expected_phase_type':'day','expected_round_number':0})
            age();post(host,base+'/chat/messages',command(channel='host',participant_user_id=ids[1],text='DM trong đêm'))
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'")
            page.get_by_text('DM trong đêm',exact=True).wait_for()
            assert 'Đêm 1' in page.locator('.u3-marker').inner_text()
            page.evaluate("location.hash='#chat/general'")
            page.get_by_text('Tin đang gửi',exact=True).wait_for()
            assert page.locator('.u3-marker').filter(has_text='Ngày 0').count()>=1
            checks.append({'case':'original-phase-markers-survive-phase-transition','result':'pass'})
            expect(page.locator('.room-topbar .room-phase')).to_have_text('Đêm 1')
            for width,height in [(1440,900),(390,844),(320,480)]:
                page.set_viewport_size({'width':width,'height':height})
                if not page.locator('#chat-text').count():
                    page.evaluate("location.hash='#chat/dm/"+ids[1]+"'")
                page.locator('#chat-text').wait_for()
                metrics=page.evaluate('''()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,composerBottom:document.querySelector('.chat-send').getBoundingClientRect().bottom})''')
                assert metrics['scrollWidth']<=width and metrics['scrollHeight']<=height and metrics['composerBottom']<=height,metrics
                page.screenshot(path=str(OUT/'screenshots'/f'chat-{width}.png'))
                checks.append({'case':f'fixed-composer-layout-{width}','result':'pass','metrics':metrics})
            page.set_viewport_size({'width':1440,'height':900})
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'")
            target=page.locator('.u3-message').filter(has_text='DM trong đêm')
            target.hover();target.get_by_role('button',name='Thao tác tin của Quản trò',exact=True).click()
            page.get_by_role('menuitem',name='Sửa',exact=True).click()
            page.get_by_label('Chỉnh tin nhắn',exact=True).fill('DM đã sửa kín')
            def delayed_revision(route):
                response=route.fetch();page.wait_for_timeout(350);route.fulfill(response=response)
            page.route('**/items/*/*/revisions',delayed_revision)
            page.evaluate("""() => {document.querySelector('.u3-edit').requestSubmit();setTimeout(()=>{location.hash='#chat/general'},50)}""")
            page.wait_for_url('**/#chat/general');page.wait_for_timeout(600)
            assert 'DM đã sửa kín' not in page.locator('.u3-message-list').inner_text()
            page.unroute('**/items/*/*/revisions',delayed_revision)
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'")
            page.locator('.u3-message-content > p').filter(has_text='DM đã sửa kín').wait_for()
            target=page.locator('.u3-message').filter(has_text='DM đã sửa kín')
            target.hover();target.get_by_role('button',name='Thao tác tin của Quản trò',exact=True).click()
            page.get_by_role('menuitem',name='Xóa',exact=True).click()
            page.locator('.confirmation-dialog[open]').get_by_role('button',name='Xóa tin',exact=True).click()
            expect(page.locator('.u3-message-content > p').filter(has_text='DM đã sửa kín')).to_have_count(0)
            checks.append({'case':'edit-delete-menu-and-delayed-edit-does-not-leak-into-general','result':'pass'})
            post(host,base+'/game/finish',{'result_label':'Thử UX3 xong','result_reason':'Kiểm tra lịch sử chat'})
            page.locator('.u3-history').wait_for()
            expect(page.locator('#chat-text')).to_have_count(0)
            assert page.locator('.u3-history').count()==1
            page.locator('.u3-history summary').click()
            assert page.locator('.u3-history li').count()==3
            assert 'DM trong đêm' in page.locator('.u3-history').inner_text()
            assert 'DM đã sửa kín' in page.locator('.u3-history').inner_text()
            checks.append({'case':'finished-readonly-transcript-original-edit-delete-retained-once','result':'pass'})
            page.get_by_role('button',name='Menu phòng Làng UX3').click()
            page.get_by_role('button',name='Về trang vào làng',exact=True).click()
            page.get_by_role('button',name='Đăng xuất',exact=True).click()
            page.get_by_role('button',name='Đăng xuất',exact=True).wait_for(state='detached')
            assert not page.evaluate("Object.keys(sessionStorage).some(key=>key.startsWith('ma-soi-chat-draft:v1:'))")
            checks.append({'case':'logout-clears-private-drafts','result':'pass'})
            assert not errors,errors
            for context in contexts:context.close()
            browser.close()
        (OUT/'browser-results.json').write_text(json.dumps({'scope':'UX3 first slice: real API, production frontend, disposable PostgreSQL14','checks':checks,'page_errors':errors},ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'checks_passed':len(checks),'page_errors':errors},ensure_ascii=False),flush=True)
    finally:
        with psycopg.connect(db_url.replace('postgresql+psycopg://','postgresql://',1)) as db:
            if room_id:db.execute('DELETE FROM rooms WHERE id=%s',(room_id,))
            for user_id in ids:db.execute('DELETE FROM users WHERE id=%s',(user_id,))
        for client in actors:client.close()

if __name__=='__main__':main()
