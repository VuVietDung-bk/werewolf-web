"""10,000 real history rows, bounded HTTP pages/DOM, geometry, reload and focus."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from psycopg.types.json import Jsonb
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
API,WEB='http://127.0.0.1:8026','http://127.0.0.1:4176'
class Checks(list):
    def append(self,value):
        super().append(value)
        print('History check passed: '+value,flush=True)
SNAPSHOT='''() => {
 const root=document.querySelector('.u3-timeline'),top=root.getBoundingClientRect().top;
 const row=[...root.querySelectorAll('[data-item-key]')].find(r=>r.getBoundingClientRect().bottom>top&&r.getBoundingClientRect().top<top+root.clientHeight);
 return {key:row.dataset.itemKey,offset:row.getBoundingClientRect().top-top,loaded:+root.dataset.loadedCount,rendered:root.querySelectorAll('.u3-message').length};
}'''


def main():
    parsed=urlsplit(os.environ.get('DATABASE_URL',''))
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=55432 or parsed.path!='/ma_soi_ux2':
        raise SystemExit('Use disposable local ma_soi_ux2 at port 55432 only')
    actors=[httpx.Client(base_url=API,trust_env=False,timeout=20) for _ in range(3)]
    host,a,b=actors;ids=[];room_id=None;checks=Checks();errors=[];metrics={};pages=[]
    out=ROOT/'docs/ux-3/screenshots';out.mkdir(parents=True,exist_ok=True)
    def post(client,path,**body):
        response=client.post(path,json=body,headers={'Origin':WEB})
        assert response.status_code in (200,201),(path,response.status_code,response.text[:250])
        return response.json()
    def command(client,path,**body):return post(client,path,client_request_id=str(uuid4()),**body)
    def connect():return psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1))
    try:
        for client,name in zip(actors,['Host lịch sử dài','An lịch sử dài','Bình lịch sử dài']):ids.append(post(client,'/api/auth/guest',display_name=name)['id'])
        room=post(host,'/api/rooms',name='Làng 10.000 tin',seat_limit=2);room_id=room['id'];base='/api/rooms/'+room_id
        for client in (a,b):post(client,'/api/rooms/join',code=room['code'],accept_postgame_transcript=True)
        post(host,base+'/roles',name='Role lịch sử',description='Thử timeline.',quantity=2,faction_label='Làng',seer_result='Làng',role_kind='active',
            request_phases=['day','night'],host_contact={'can_initiate':True,'initiate_phases':['day','night'],'reply_allowed':True})
        response=host.patch(base+'/settings',json={'name':room['name'],'seat_limit':2,'registration_open':False},headers={'Origin':WEB});assert response.status_code==200
        post(host,base+'/roles/distribute-random');post(host,base+'/game/start')
        cat=base.replace('/api/rooms/','/api/v2/rooms/')+'/channels'
        common=next(item for item in host.get(cat).json()['channels'] if item['kind']=='common');url=cat+'/'+common['id']
        # Fixture-only bulk seed, before clients connect. Persist sources, events, outbox
        # and all public deliveries consistently; live edits/messages still use the API.
        with connect() as db:
            version,public=db.execute('SELECT version,public_version FROM rooms WHERE id=%s FOR UPDATE',(room_id,)).fetchone()
            streams=dict(db.execute('SELECT user_id,position FROM room_delivery_streams WHERE room_id=%s',(room_id,)).fetchall())
            rows=[];start=datetime.now(timezone.utc)-timedelta(days=1)
            for i in range(10000):
                text=f'Lịch sử {i:05d} 🐺'+ ('\n'+'Đo chiều cao thay đổi, nhiều dòng. '*12 if i%17==0 else '')
                rows.append((uuid4(),uuid4(),version+i+1,ids[i%2],text,start+timedelta(seconds=i)))
            with db.cursor().copy('COPY game_events (id,room_id,sequence,actor_user_id,kind,visibility,payload,created_at) FROM STDIN') as copy:
                for source,event,seq,sender,text,created in rows:copy.write_row((event,room_id,seq,sender,'chat.sent','room',Jsonb({'channel':'common','phase_type':'day','round_number':0,'recipient_ids':[]}),created))
            with db.cursor().copy('COPY chat_messages (id,room_id,event_id,sequence,updated_sequence,channel,sender_user_id,text,created_at,recipient_ids) FROM STDIN') as copy:
                for source,event,seq,sender,text,created in rows:copy.write_row((source,room_id,event,seq,seq,'common',sender,text,created,Jsonb([])))
            with db.cursor().copy('COPY outbox (id,event_id,created_at,published_at,attempts) FROM STDIN') as copy:
                for _,event,_,_,_,created in rows:copy.write_row((uuid4(),event,created,created,0))
            with db.cursor().copy('COPY room_deliveries (id,room_id,user_id,position,source_event_id,scopes,channel_ids) FROM STDIN') as copy:
                for user,position in streams.items():
                    for index,(_,event,*_) in enumerate(rows):copy.write_row((uuid4(),room_id,user,position+index+1,event,Jsonb(['channels','chat']),Jsonb([common['id']])))
            db.execute('UPDATE room_delivery_streams SET position=position+10000 WHERE room_id=%s',(room_id,))
            db.execute('UPDATE rooms SET version=%s,public_version=%s WHERE id=%s',(version+10000,public+10000,room_id))
        print('History fixture ready; checking channel discovery',flush=True)
        discovery_started=time.monotonic()
        discovery=host.get(cat,timeout=60)
        assert discovery.status_code==200,discovery.text[:300]
        metrics['discovery_seconds']=round(time.monotonic()-discovery_started,3)
        print('History discovery seconds: '+str(metrics['discovery_seconds']),flush=True)
        assert metrics['discovery_seconds']<5,metrics
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('UX2_CHROMIUM','/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'),args=['--no-sandbox','--disable-dev-shm-usage'])
            context=browser.new_context(viewport={'width':1440,'height':900})
            context.add_cookies([{'name':'ma_soi_session','value':host.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
            context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+')')
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('console',lambda msg:print(msg.text,flush=True) if msg.text.startswith('History loaded:') else None)
            def received(response):
                if '/timeline' in response.url and response.status==200:
                    data=response.json();pages.append({'url':response.url,'items':len(data['items'])})
            page.on('response',received)
            page.goto(WEB+'/#chat/general')
            try:page.locator('.u3-timeline[data-loaded-count="30"]').wait_for()
            except Exception:
                print(json.dumps({'page_errors':errors,'pages':pages,'body':page.locator('body').inner_text()[:3000]}),flush=True)
                raise
            page.wait_for_timeout(200)
            cdp=context.new_cdp_session(page);cdp.send('HeapProfiler.collectGarbage')
            metrics['initial_heap_bytes']=cdp.send('Runtime.getHeapUsage')['usedSize']
            started=time.monotonic()
            page.evaluate('''async()=>{
              const root=document.querySelector('.u3-timeline');
              while(+root.dataset.loadedCount<10000){
                const previous=+root.dataset.loadedCount,button=document.querySelector('.u3-load-older');
                if(!button)throw Error('History cursor ended too soon');button.click();
                await new Promise((resolve,reject)=>{const limit=setTimeout(()=>{observer.disconnect();reject(Error('History load stalled'))},15000);
                  const observer=new MutationObserver(()=>{if(+root.dataset.loadedCount>previous){observer.disconnect();clearTimeout(limit);resolve()}});
                  observer.observe(root,{attributes:true,attributeFilter:['data-loaded-count']});});
                if(+root.dataset.loadedCount%1000<30)console.info('History loaded: '+root.dataset.loadedCount);
                await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
              }
            }''')
            metrics['load_10000_seconds']=round(time.monotonic()-started,3)
            assert all(page['items']<=50 for page in pages)
            metrics['history_http_pages']=len(pages)
            cdp.send('HeapProfiler.collectGarbage');metrics['loaded_heap_bytes']=cdp.send('Runtime.getHeapUsage')['usedSize']
            metrics['loaded_dom_nodes']=page.locator('*').count()
            checks.append('10000-history-items-loaded-through-bounded-http-pages')
            samples=page.evaluate('''async()=>{
              const root=document.querySelector('.u3-timeline'),samples=[];
              for(let i=0;i<90;i++){
                const start=performance.now();root.scrollTop=(root.scrollHeight-root.clientHeight)*(i%30)/29;
                await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                samples.push({milliseconds:performance.now()-start,rendered:root.querySelectorAll('.u3-message').length});
              }return samples;
            }''')
            metrics['max_rendered_rows']=max(sample['rendered'] for sample in samples)
            metrics['scroll_two_frames_p95_ms']=round(sorted(sample['milliseconds'] for sample in samples)[int(len(samples)*.95)-1],2)
            assert metrics['max_rendered_rows']<160,metrics
            checks.append('scroll-10000-items-keeps-bounded-dom-with-variable-height')
            root=page.locator('.u3-timeline')
            target=page.locator('.u3-message').filter(has_text='Lịch sử 09998');target.hover()
            target.get_by_role('button',name='Thao tác tin của Quản trò',exact=True).click();page.get_by_role('menuitem',name='Sửa',exact=True).click()
            editor=page.get_by_label('Chỉnh tin nhắn',exact=True);expect(editor).to_be_focused()
            root.evaluate('(r)=>{r.scrollTop=0;r.dispatchEvent(new Event("scroll"))}');page.wait_for_timeout(250)
            expect(editor).to_be_focused();expect(editor).to_have_count(1)
            assert page.locator('.u3-message').count()<160
            page.get_by_role('button',name='Hủy',exact=True).click();page.locator('#chat-text').focus()
            checks.append('focused-editor-survives-window-movement-in-10000-item-history')
            root.evaluate('(root)=>{root.scrollTop=root.scrollHeight*.48;root.dispatchEvent(new Event("scroll"))}')
            page.wait_for_timeout(500)
            def snapshot():return page.evaluate(SNAPSHOT)
            def retained(saved):
                target=page.locator('[data-item-key="'+saved['key']+'"]');target.wait_for()
                page.wait_for_timeout(300)
                offset=target.evaluate('(row)=>row.getBoundingClientRect().top-document.querySelector(".u3-timeline").getBoundingClientRect().top')
                assert abs(offset-saved['offset'])<3,(saved,offset,snapshot())
            saved=snapshot()
            page.screenshot(path=str(out/'virtual-10000-desktop.png'))
            for width,height in [(390,844),(320,480)]:
                page.set_viewport_size({'width':width,'height':height});retained(saved)
                metrics[f'mobile_{width}_rendered_rows']=page.locator('.u3-message').count()
                assert metrics[f'mobile_{width}_rendered_rows']<160
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                cdp.send('HeapProfiler.collectGarbage');metrics[f'mobile_{width}_heap_bytes']=cdp.send('Runtime.getHeapUsage')['usedSize']
                page.screenshot(path=str(out/f'virtual-10000-{width}.png'))
            page.set_viewport_size({'width':1440,'height':900});retained(saved)
            checks.append('10000-item-mobile-window-stays-bounded-and-resize-keeps-anchor')
            command(b,base+'/chat/messages',channel='common',text='Tin chưa đọc ngoài cửa sổ render')
            page.locator('.u3-timeline[data-loaded-count="10001"]').wait_for();retained(saved)
            assert page.get_by_text('Tin chưa đọc ngoài cửa sổ render',exact=True).count()==0
            assert next(item for item in host.get(cat).json()['channels'] if item['id']==common['id'])['unread']
            page.get_by_role('button',name='1 tin mới · Đến cuối ↓',exact=True).click()
            page.get_by_text('Tin chưa đọc ngoài cửa sổ render',exact=True).wait_for()
            for _ in range(100):
                if not next(item for item in host.get(cat).json()['channels'] if item['id']==common['id'])['unread']:break
                page.wait_for_timeout(100)
            else:raise AssertionError('Visible virtual tail did not acknowledge the new message')
            checks.append('unmounted-tail-stays-unread-until-user-jumps-to-visible-row')
            root.evaluate('(root)=>{root.scrollTop=root.scrollHeight*.48;root.dispatchEvent(new Event("scroll"))}')
            page.wait_for_timeout(500)
            # Use a host-authored visible source as the later deletion fixture.
            root.evaluate('''root=>{const top=root.getBoundingClientRect().top;
                const row=[...root.querySelectorAll('.u3-message')].find(row=>row.querySelector('.chat-host-badge')&&row.getBoundingClientRect().top>=top&&row.getBoundingClientRect().top<top+root.clientHeight);
                if(!row)throw Error('Expected a visible host row');root.scrollTop+=row.getBoundingClientRect().top-top+2;
                root.dispatchEvent(new Event('scroll'));}''')
            page.wait_for_timeout(200);saved=snapshot()
            above=page.locator('.u3-message').evaluate_all('''rows=>{const top=document.querySelector('.u3-timeline').getBoundingClientRect().top;
                return rows.filter(row=>row.getBoundingClientRect().bottom<=top&&row.getBoundingClientRect().top>top-500&&row.querySelector('.chat-host-badge')).map(row=>row.dataset.itemKey)}''')
            assert above
            changed=above[-1]
            command(host,url+'/items/'+changed.replace(':','/')+'/revisions',action='edit',text='Tin sửa dài phía trên mốc đọc\n'+'Nội dung mới thay đổi chiều cao. '*30)
            page.wait_for_timeout(600);retained(saved)
            response=host.put(url+'/items/'+changed.replace(':','/')+'/reactions',json={'client_request_id':str(uuid4()),'emoji':'🐺'},headers={'Origin':WEB})
            assert response.status_code==200,response.text
            page.wait_for_timeout(600);retained(saved)
            checks.append('live-edit-and-reaction-height-changes-above-viewport-preserve-anchor')
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'");page.locator('.u3-empty').wait_for()
            page.evaluate("location.hash='#chat/general'");retained(saved)
            checks.append('switch-channel-restores-middle-anchor-with-10000-cached-items')
            page.get_by_role('button',name='Role',exact=True).click()
            expect(page.locator('.u3-timeline')).to_have_count(0)
            page.get_by_role('button',name='Chat',exact=True).click();retained(saved)
            assert int(root.get_attribute('data-loaded-count'))==30
            checks.append('workspace-unmount-restores-anchor-with-one-bounded-page')
            pages.clear();page.reload();retained(saved)
            assert len(pages)==1 and 'around_item=' in pages[0]['url'] and pages[0]['items']==30,pages
            checks.append('reload-seeks-authorized-source-without-history-scan')
            # Middle window revisions remain live, while new tail messages wait behind the gap.
            visible=host.get(url+'/timeline',params={'around_item':saved['key']}).json()['items']
            command(host,base+'/chat/messages',channel='common',text='Tin mới ở cuối khi đang đọc xa')
            page.wait_for_timeout(800);retained(saved)
            assert page.get_by_text('Tin mới ở cuối khi đang đọc xa',exact=True).count()==0
            expect(page.get_by_role('button',name='Đến tin mới nhất ↓',exact=True)).to_be_visible()
            next_button=page.get_by_role('button',name='Xem tin tiếp theo',exact=True)
            next_button.scroll_into_view_if_needed();page.wait_for_timeout(100);newer_saved=snapshot()
            next_button.click()
            page.locator('.u3-timeline[data-loaded-count="60"]').wait_for();retained(newer_saved)
            loaded=[row['id'] for row in host.get(url+'/timeline',params={'around_item':saved['key'],'limit':50}).json()['items']]
            assert loaded[:30]==[row['id'] for row in visible]
            page.get_by_role('button',name='Đến tin mới nhất ↓',exact=True).click()
            page.get_by_text('Tin mới ở cuối khi đang đọc xa',exact=True).wait_for()
            assert int(root.get_attribute('data-loaded-count'))==30
            assert root.evaluate('(r)=>r.scrollHeight-r.scrollTop-r.clientHeight')<3
            checks.append('middle-window-forward-paging-stays-contiguous-and-jump-latest-fetches-one-page')
            # Reopen an old window, resize to mobile and send: sending must reach the actual tail.
            seed=[[json.dumps([ids[0],room_id,common['id']],separators=(',',':')),
                {'key':saved['key'],'offset':saved['offset'],'top':0,'atEnd':False}]]
            page.add_init_script("sessionStorage.setItem('ma-soi-chat-positions:v2',"+json.dumps(json.dumps(seed))+')')
            page.reload();retained(saved)
            page.set_viewport_size({'width':320,'height':480});retained(saved)
            page.set_viewport_size({'width':320,'height':360});retained(saved)
            page.set_viewport_size({'width':320,'height':480});retained(saved)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            checks.append('mobile-width-and-keyboard-height-preserve-source-anchor')
            page.locator('#chat-text').fill('Tự gửi khi đang đọc lịch sử');page.locator('#chat-text').press('Enter')
            page.get_by_text('Tự gửi khi đang đọc lịch sử',exact=True).wait_for()
            page.wait_for_function('''()=>{const r=document.querySelector('.u3-timeline');return r.scrollHeight-r.scrollTop-r.clientHeight<3}''',timeout=3000)
            page.set_viewport_size({'width':320,'height':360});page.wait_for_timeout(200)
            assert root.evaluate('(r)=>r.scrollHeight-r.scrollTop-r.clientHeight')<3
            checks.append('send-from-restored-history-jumps-to-actual-latest-page')
            page.set_viewport_size({'width':1440,'height':900})
            # Delete a saved source while away; fallback must not loop a missing seek.
            page.evaluate("location.hash='#chat/dm/"+ids[1]+"'");page.locator('.u3-empty').wait_for()
            command(host,url+'/items/'+saved['key'].replace(':','/')+'/revisions',action='delete')
            page.evaluate("location.hash='#chat/general'")
            page.reload();page.get_by_text('Tự gửi khi đang đọc lịch sử',exact=True).wait_for()
            checks.append('deleted-saved-anchor-falls-back-to-latest-without-retry-loop')
            out=ROOT/'docs/ux-3/screenshots';out.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(out/'virtual-history-desktop.png'))
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'virtual-history-mobile.png'))
            # Final forward page has no more rows in its snapshot. A concurrent
            # creation is first observed while the window still has a gap, then
            # must be replayed after the delayed page closes that gap.
            race_context=browser.new_context(viewport={'width':1280,'height':800})
            race_context.add_cookies([{'name':'ma_soi_session','value':host.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
            race_anchor={'key':'chat:'+str(rows[-50][0]),'offset':0,'top':0,'atEnd':False}
            race_seed=[[json.dumps([ids[0],room_id,common['id']],separators=(',',':')),race_anchor]]
            race_context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+");sessionStorage.setItem('ma-soi-chat-positions:v2',"+json.dumps(json.dumps(race_seed))+')')
            rp=race_context.new_page();rp.set_default_timeout(15000);rp.on('pageerror',lambda error:errors.append(str(error)))
            observed=[]
            def changed_response(response):
                if '/changes?' in response.url and response.status==200:
                    observed.extend(change['item']['text'] for change in response.json()['changes'])
            rp.on('response',changed_response)
            rp.goto(WEB+'/#chat/general');rp.locator('.u3-timeline[data-loaded-count="30"]').wait_for()
            held=[]
            def delay_final(route):
                response=route.fetch();assert response.json()['next_after'] is None
                held.append((route,response))
            rp.route('**/timeline?after=*',delay_final)
            rp.get_by_role('button',name='Xem tin tiếp theo',exact=True).click()
            for _ in range(100):
                if held:break
                rp.wait_for_timeout(50)
            assert held
            command(host,base+'/chat/messages',channel='common',text='Tin mới đua trang cuối')
            for _ in range(100):
                if 'Tin mới đua trang cuối' in observed:break
                rp.wait_for_timeout(100)
            assert 'Tin mới đua trang cuối' in observed
            assert rp.get_by_text('Tin mới đua trang cuối',exact=True).count()==0
            held[0][0].fulfill(response=held[0][1])
            expect(rp.get_by_text('Tin mới đua trang cuối',exact=True)).to_have_count(1)
            checks.append('arrival-after-last-forward-snapshot-is-replayed-once-when-gap-closes')
            initial_context=browser.new_context(viewport={'width':1280,'height':800})
            initial_context.add_cookies([{'name':'ma_soi_session','value':host.cookies['ma_soi_session'],'url':WEB,'httpOnly':True}])
            initial_context.add_init_script("localStorage.setItem('ma-soi-active-room',"+json.dumps(room_id)+");sessionStorage.setItem('ma-soi-chat-positions:v2',"+json.dumps(json.dumps(race_seed))+')')
            ip=initial_context.new_page();ip.set_default_timeout(15000);ip.on('pageerror',lambda error:errors.append(str(error)))
            pending_initial=[]
            def delay_initial(route):pending_initial.append((route,route.fetch()))
            ip.route('**/timeline?around_item=*',delay_initial)
            ip.goto(WEB+'/#chat/general');ip.locator('#chat-text').wait_for()
            for _ in range(100):
                if pending_initial:break
                ip.wait_for_timeout(50)
            assert pending_initial
            ip.locator('#chat-text').fill('Gửi ngay khi lịch sử còn chờ');ip.locator('#chat-text').press('Enter')
            ip.get_by_text('Gửi ngay khi lịch sử còn chờ',exact=True).wait_for()
            ip.wait_for_function('''()=>{const r=document.querySelector('.u3-timeline');return r.scrollHeight-r.scrollTop-r.clientHeight<3}''')
            pending_initial[0][0].fulfill(response=pending_initial[0][1]);ip.wait_for_timeout(300)
            expect(ip.get_by_text('Gửi ngay khi lịch sử còn chờ',exact=True)).to_have_count(1)
            assert ip.get_by_role('button',name='Xem tin tiếp theo',exact=True).count()==0
            checks.append('send-during-initial-history-load-follows-tail-and-discards-stale-window')
            response=host.post('/api/auth/logout',headers={'Origin':WEB})
            assert response.status_code in (200,204)
            expect(ip.locator('.room-layout')).to_have_count(0,timeout=15000)
            ip.wait_for_timeout(300)
            assert ip.evaluate("sessionStorage.getItem('ma-soi-chat-positions:v2')") is None
            checks.append('session-revocation-clears-position-metadata-without-unmount-repopulation')
            assert not errors,errors
            browser.close()
        result={'scope':'UX3 virtual history: Chromium + PostgreSQL14, 10000 real fixture messages','checks':checks,'metrics':metrics,'page_errors':errors,
            'note':'Fixture bulk insertion models past committed public history; pagination, live messages/revisions/read ACK use real HTTP. Headless local measurements are not a 500-user or physical-phone load test.'}
        (ROOT/'docs/ux-3/virtual-browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        if room_id:
            with connect() as db:
                db.execute('DELETE FROM rooms WHERE id=%s',(room_id,))
                for uid in ids:db.execute('DELETE FROM users WHERE id=%s',(uid,))
        for client in actors:client.close()


if __name__=='__main__':main()
