"""Native emoji reactions in real browser: realtime, retry, privacy and postgame."""
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
        raise SystemExit('Use disposable local ma_soi_ux2 at localhost:55432 only')
    actors=[httpx.Client(base_url=API,trust_env=False,timeout=15) for _ in range(3)]
    host,a,b=actors;ids=[];room_id=None;checks=[];errors=[]
    def post(client,path,**body):
        result=client.post(path,json=body,headers={'Origin':WEB})
        assert result.status_code in (200,201),(path,result.status_code,result.text[:200])
        return result.json()
    def command(client,path,**body):return post(client,path,client_request_id=str(uuid4()),**body)
    try:
        for client,name in zip(actors,['Host reaction','An reaction','Bình reaction']):ids.append(post(client,'/api/auth/guest',display_name=name)['id'])
        room=post(host,'/api/rooms',name='Làng reaction',seat_limit=2);room_id=room['id'];base='/api/rooms/'+room_id
        for client in (a,b):post(client,'/api/rooms/join',code=room['code'],accept_postgame_transcript=True)
        post(host,base+'/roles',name='Role reaction',description='Dùng chat để thử reaction.',quantity=2,faction_label='Làng',seer_result='Làng',
            team_chat_label='Đội phản ứng',role_kind='active',request_phases=['day','night'],
            host_contact={'can_initiate':True,'initiate_phases':['day','night'],'reply_allowed':True})
        response=host.patch(base+'/settings',json={'name':room['name'],'seat_limit':2,'registration_open':False},headers={'Origin':WEB});assert response.status_code==200
        post(host,base+'/roles/distribute-random');post(host,base+'/game/start')
        cat=base.replace('/api/rooms/','/api/v2/rooms/')+'/channels'
        def channels(client):return client.get(cat).json()['channels']
        common=next(item for item in channels(a) if item['kind']=='common')
        dm=next(item for item in channels(a) if item['kind']=='moderator_dm')
        def source_path(channel,source):return cat+'/'+channel['id']+'/items/'+source['kind']+'/'+source['id']+'/reactions'
        def source_for(client,channel,text):
            return next(item['source'] for item in client.get(cat+'/'+channel['id']+'/timeline').json()['items'] if item['text']==text)
        original='Tin phản ứng <b>vẫn là text</b>'
        command(host,base+'/chat/messages',channel='common',text=original)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome',args=['--no-sandbox','--disable-dev-shm-usage'])
            frames={}
            def page_for(client):
                context=browser.new_context(viewport={'width':1440,'height':900})
                context.add_cookies([{'name':'ma_soi_session','value':client.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
                context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+')')
                page=context.new_page();page.set_default_timeout(12000);frames[page]=[]
                def socket_open(socket):
                    def receive(data):
                        try:frames[page].append(json.loads(data))
                        except (ValueError,TypeError):pass
                    socket.on('framereceived',receive)
                page.on('websocket',socket_open);page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(WEB+'/#chat/general');page.locator('.room-live-state[data-state=live]').wait_for()
                return page
            ap,bp,hp=page_for(a),page_for(b),page_for(host)
            def row(page,text):return page.locator('.u3-message').filter(has=page.locator('.u3-message-content > p').filter(has_text=text))
            def chip(message,emoji):return message.get_by_role('button',name=re.compile('^Reaction '+re.escape(emoji)+':'))
            def pick(page,message,emoji,keyboard=False):
                message.scroll_into_view_if_needed();message.hover()
                message.get_by_role('button',name=re.compile('Thêm reaction')).click()
                page.get_by_label('Tìm emoji',exact=True).fill(emoji)
                if keyboard:
                    page.get_by_label('Tìm emoji',exact=True).press('ArrowDown');page.keyboard.press('Enter')
                else:page.locator('.u3-emoji-dialog [data-emoji]').first.click()
            family='👨‍👩‍👧‍👦'
            ar,br=row(ap,original),row(bp,original)
            pick(ap,ar,family)
            expect(chip(ar,family)).to_have_attribute('aria-pressed','true')
            expect(chip(br,family)).to_have_text(family+'1')
            assert ap.locator('.u3-message-content b').count()==0
            checks.append('unicode-zwj-family-kept-whole-safe-text-and-realtime-count')
            chip(br,family).click();expect(chip(ar,family)).to_have_text(family+'2')
            chip(ar,family).click();expect(chip(ar,family)).to_have_attribute('aria-pressed','false')
            expect(chip(br,family)).to_have_text(family+'1')
            chip(ar,family).click();expect(chip(br,family)).to_have_text(family+'2')
            checks.append('two-actors-share-count-and-toggle-only-own-reaction')

            pick(ap,ar,'🐺',keyboard=True);expect(chip(ar,'🐺')).to_have_text('🐺1')
            ar.hover();trigger=ar.get_by_role('button',name=re.compile('Thêm reaction'))
            trigger.click();ap.keyboard.press('Escape');expect(trigger).to_be_focused()
            checks.append('picker-search-keyboard-and-escape-focus')
            bp.get_by_role('navigation',name='Điều hướng phòng').get_by_role('button',name='Role',exact=True).click()
            chip(ar,'🐺').click();expect(chip(ar,'🐺')).to_have_count(0)
            assert not next(item for item in channels(b) if item['kind']=='common')['unread']
            bp.get_by_role('navigation',name='Điều hướng phòng').get_by_role('button',name='Chat',exact=True).click()
            checks.append('reaction-change-does-not-create-unread-chat')

            ap.wait_for_timeout(11000)  # Reset the real burst window after the toggle/keyboard cases.
            command(host,base+f'/game/players/{ids[1]}/private-messages',text='Một chiều cho An')
            ap.locator('.room-sidebar').get_by_role('button',name=re.compile('Quản trò')).click()
            private=row(ap,'Một chiều cho An');private.wait_for()
            before=len(frames[bp]);before_catalog=channels(b)
            pick(ap,private,'🐺');expect(chip(private,'🐺')).to_have_text('🐺1')
            ap.wait_for_timeout(700)
            assert len(frames[bp])==before and channels(b)==before_catalog
            assert bp.get_by_text('Một chiều cho An',exact=True).count()==0
            checks.append('private-message-reaction-does-not-leak-frame-or-catalog-to-third-player')
            command(host,base+'/chat/messages',channel='host',participant_user_id=ids[1],text='Hai chiều cho An')
            second=row(ap,'Hai chiều cho An');second.wait_for()
            pick(ap,second,'🐺');expect(chip(second,'🐺')).to_have_text('🐺1')
            hp.locator('.room-sidebar').get_by_role('button',name='An reaction',exact=True).click()
            expect(chip(row(hp,'Hai chiều cho An'),'🐺')).to_have_text('🐺1')
            expect(chip(row(hp,'Một chiều cho An'),'🐺')).to_have_text('🐺1')
            checks.append('one-way-and-two-way-dm-sources-react-independently-for-both-sides')

            command(host,base+'/game/advance',expected_phase_type='day',expected_round_number=0)
            ap.locator('.room-sidebar').get_by_text('Đội phản ứng',exact=True).click()
            bp.locator('.room-sidebar').get_by_text('Đội phản ứng',exact=True).click()
            command(host,base+'/chat/messages',channel='team',team_label='Đội phản ứng',text='Reaction đội ban đêm')
            team_row=row(ap,'Reaction đội ban đêm');team_row.wait_for()
            pick(ap,team_row,'🐺');expect(chip(row(bp,'Reaction đội ban đêm'),'🐺')).to_have_text('🐺1')
            command(host,base+'/game/advance',expected_phase_type='night',expected_round_number=1)
            expect(chip(team_row,'🐺')).to_be_disabled()
            expect(team_row.get_by_role('button',name=re.compile('Thêm reaction'))).to_have_count(0)
            checks.append('team-reaction-follows-day-night-channel-policy')

            ap.locator('.room-sidebar button .room-entry-name').get_by_text('general',exact=True).click()
            ap.locator('#chat-text').fill('Nháp giữ qua reaction và mute')
            response=host.patch(base+f'/chat/players/{ids[1]}/mute',json={'client_request_id':str(uuid4()),'muted':True,'reason':'Reaction mute'},headers={'Origin':WEB});assert response.status_code==200
            expect(chip(row(ap,original),family)).to_be_disabled()
            ap.reload();ap.locator('.room-live-state[data-state=live]').wait_for()
            expect(chip(row(ap,original),family)).to_be_disabled()
            response=host.patch(base+f'/chat/players/{ids[1]}/mute',json={'client_request_id':str(uuid4()),'muted':False,'reason':'Restore'},headers={'Origin':WEB});assert response.status_code==200
            expect(chip(row(ap,original),family)).to_be_enabled()
            expect(ap.locator('#chat-text')).to_have_value('Nháp giữ qua reaction và mute')
            checks.append('mute-blocks-reaction-and-unmute-refreshes-controls-with-draft-intact')

            ap.wait_for_timeout(11000)  # Honor the actual API burst window across the previous UI operations.
            command(host,base+'/chat/messages',channel='common',text='Tin kiểm tra retry')
            retry_row=row(ap,'Tin kiểm tra retry');retry_row.wait_for()
            seen=[];fail={'next':True}
            def lost_ack(route):
                if route.request.method=='PUT':
                    body=route.request.post_data_json;seen.append((body['emoji'],body['client_request_id']))
                    if fail['next']:
                        fail['next']=False;route.fetch();route.fulfill(status=503,json={'detail':'Mất ACK reaction thử'});return
                route.continue_()
            ap.route('**/items/*/*/reactions',lost_ack)
            pick(ap,retry_row,'🐺');ap.get_by_role('alert').filter(has_text='Mất ACK reaction thử').wait_for()
            expect(chip(retry_row,'🐺')).to_have_text('🐺1')
            pick(ap,retry_row,'🐺');expect(retry_row.get_by_role('button',name=re.compile('Thêm reaction'))).to_be_enabled()
            wolf_ids=[request for emoji,request in seen if emoji=='🐺']
            assert len(wolf_ids)==2 and wolf_ids[0]==wolf_ids[1]
            fail['next']=True
            pick(ap,retry_row,family);ap.get_by_role('alert').filter(has_text='Mất ACK reaction thử').wait_for()
            expect(chip(retry_row,family)).to_have_attribute('aria-pressed','true')
            chip(retry_row,family).click();expect(chip(retry_row,family)).to_have_count(0)
            pick(ap,retry_row,family);expect(chip(retry_row,family)).to_have_text(family+'1')
            family_ids=[request for emoji,request in seen if emoji==family]
            assert len(family_ids)==2 and family_ids[0]!=family_ids[1]
            ap.unroute('**/items/*/*/reactions',lost_ack)
            checks.append('lost-ack-retries-same-id-and-new-opposite-intent-invalidates-stale-command')

            held=[]
            def delay(route):
                if route.request.method=='PUT' and not held:held.append(route)
                else:route.continue_()
            ap.route('**/items/*/*/reactions',delay)
            pick(ap,retry_row,'❤️')
            for _ in range(50):
                if held:break
                ap.wait_for_timeout(100)
            assert held
            ap.locator('.room-sidebar').get_by_role('button',name=re.compile('Quản trò')).click()
            row(ap,'Một chiều cho An').wait_for();held[0].continue_();ap.wait_for_timeout(500)
            assert ap.locator('.u3-reaction').filter(has_text='❤️').count()==0
            ap.unroute('**/items/*/*/reactions',delay)
            ap.locator('.room-sidebar button .room-entry-name').get_by_text('general',exact=True).click()
            expect(chip(row(ap,'Tin kiểm tra retry'),'❤️')).to_have_text('❤️1')
            checks.append('late-reaction-response-does-not-enter-a-different-channel')

            # Host controls have two tools; verify compact mobile layout and keep a screenshot.
            hp.locator('.room-sidebar button .room-entry-name').get_by_text('general',exact=True).click()
            for width in (320,390):
                hp.set_viewport_size({'width':width,'height':844})
                assert hp.evaluate('document.documentElement.scrollWidth<=innerWidth')
                geometry=hp.locator('.u3-double-tools').first.evaluate('''element => {
                    const content=element.querySelector('.u3-message-content').getBoundingClientRect();
                    const tools=element.querySelector('.u3-message-tools').getBoundingClientRect();
                    const trigger=element.querySelector('.u3-react-trigger');
                    return {contentRight:content.right,toolsLeft:tools.left,
                        triggerScroll:trigger.scrollWidth,triggerClient:trigger.clientWidth};
                }''')
                assert geometry['contentRight']<=geometry['toolsLeft'] and geometry['triggerScroll']<=geometry['triggerClient'], (width,geometry)
            out=ROOT/'docs/ux-3/screenshots';out.mkdir(parents=True,exist_ok=True)
            hp.screenshot(path=str(out/'reactions-host-mobile.png'))
            hp.set_viewport_size({'width':1440,'height':900})
            retry_source=source_for(a,common,'Tin kiểm tra retry')
            # A fresh command requesting an already-present reaction is an audited no-op.
            response=a.put(source_path(common,retry_source),json={'client_request_id':str(uuid4()),'emoji':'🐺'},headers={'Origin':WEB})
            assert response.status_code==200
            original_source=source_for(host,common,original)
            command(host,cat+f"/{common['id']}/items/{original_source['kind']}/{original_source['id']}/revisions",action='delete')
            expect(row(ap,original)).to_have_count(0)
            command(host,base+'/game/finish',result_label='Đã thử reaction',result_reason='Kiểm tra lịch sử và khóa sau trận')
            ap.get_by_text('Đã kết thúc · Chỉ đọc',exact=True).first.wait_for()
            deleted=ap.locator('.u3-message').filter(has=ap.locator('.u3-message-content > p').filter(has_text='Tin nhắn đã xóa'))
            expect(chip(deleted,family)).to_have_text(family+'2');expect(chip(deleted,family)).to_be_disabled()
            assert ap.locator('[aria-label^="Thêm reaction cho tin"]').count()==0
            events=a.get(base+'/transcript',params={'category':'chat','limit':100}).json()['events']
            assert any(event['kind']=='chat.reaction_decided' and event['payload'].get('changed') is False for event in events)
            assert any(event['kind']=='chat.reaction_changed' and event['payload'].get('present') is False for event in events)
            checks.append('deleted-source-retains-reactions-postgame-and-full-journal-keeps-add-remove-noop')
            assert not errors,errors
            ap.screenshot(path=str(out/'reactions-postgame.png'));browser.close()
        result={'scope':'UX3 reactions: real Chromium/HTTP/WS/PostgreSQL14',
            'checks':[{'case':case,'result':'pass'} for case in checks],'page_errors':errors}
        (ROOT/'docs/ux-3/reaction-browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'reaction_checks_passed':len(checks),'page_errors':errors}),flush=True)
    finally:
        if room_id:
            with psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')) as db:
                db.execute('DELETE FROM rooms WHERE id=%s',(room_id,))
                for uid in ids:db.execute('DELETE FROM users WHERE id=%s',(uid,))
        for client in actors:client.close()


if __name__=='__main__':main()
