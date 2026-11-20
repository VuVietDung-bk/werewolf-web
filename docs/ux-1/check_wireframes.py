"""Review only UX1's offline prototype; no API or database is contacted."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
URL = (ROOT / 'wireframes.html').as_uri()
CHROME = os.environ.get('UX1_CHROMIUM', '/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome')

def main():
    checks = []
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                   args=['--no-sandbox', '--disable-dev-shm-usage'], timeout=15000)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        context.route('http://**', lambda route: route.abort())
        context.route('https://**', lambda route: route.abort())
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_default_timeout(5000)

        def load(route='chat/general', actor='host', stage='playing'):
            page.goto(f'{URL}?actor={actor}&stage={stage}#{route}')
            page.wait_for_selector('#center .channel-header')

        def shot(name):
            page.screenshot(path=str(ROOT / 'screenshots' / f'{name}.png'))

        for width, height in [(1440, 900), (1024, 768), (390, 844), (320, 740)]:
            page.set_viewport_size({'width': width, 'height': height})
            load()
            data = page.evaluate('''() => ({
                width:innerWidth,bodyWidth:document.documentElement.scrollWidth,
                bodyHeight:document.documentElement.scrollHeight,height:innerHeight,
                tabs:[...document.querySelectorAll('.top nav button')].map(b=>b.textContent),
                composer:document.querySelector('.composer').getBoundingClientRect().bottom,
                sidebar:getComputedStyle(document.querySelector('.side')).display,
                members:getComputedStyle(document.querySelector('.members')).display
            })''')
            assert data['bodyWidth'] <= width, data
            assert data['bodyHeight'] <= height, data
            assert data['tabs'] == ['Chat', 'Role', 'Quản lý'], data
            assert data['composer'] <= height, data
            if width < 1200:
                assert data['members'] == 'none', data
            if width < 760:
                assert data['sidebar'] == 'none', data
                page.locator('#channels-toggle').click()
                assert page.locator('#dialog').evaluate('(d)=>d.open')
                page.keyboard.press('Escape')
                assert not page.locator('#dialog').evaluate('(d)=>d.open')
                page.locator('#members-toggle').click()
                page.keyboard.press('Escape')
            shot(f'chat-{width}')
            checks.append({'case':f'layout-{width}', 'result':'pass', 'metrics':data})

        page.set_viewport_size({'width':1440,'height':900})
        load()
        page.locator('#compose-text').fill('Bản nháp chưa gửi')
        page.locator('#sidebar [data-route="chat/announcements"]').click()
        page.wait_for_function("location.hash === '#chat/announcements'")
        page.locator('#sidebar [data-route="chat/general"]').click()
        page.wait_for_function("location.hash === '#chat/general'")
        assert page.locator('#compose-text').input_value() == 'Bản nháp chưa gửi'
        page.locator('#emoji').click()
        page.locator('#dialog .emoji-grid button').first.click()
        assert '🙂' in page.locator('#compose-text').input_value()
        page.locator('#compose-text').fill('Thử Unicode 🐺 và <script>văn bản</script>')
        page.locator('#compose-text').press('Enter')
        assert page.locator('#timeline').inner_text().count('Thử Unicode') == 1
        checks.append({'case':'draft-switch-emoji-send-text','result':'pass'})

        load('role/catalog',stage='lobby')
        page.locator('[data-edit-role="0"]').click()
        before=page.locator('.description-field').bounding_box()['height']
        page.locator('#role-kind').select_option(label='Bị động')
        assert page.locator('.description-field').bounding_box()['height'] == before
        shot('role-editor-1440')
        page.keyboard.press('Escape')
        page.locator('#role-search').fill('Sói')
        assert page.locator('[data-role-row]:visible').count() == 1
        checks.append({'case':'role-editor-fixed-description-and-search','result':'pass'})
        load('role/mine',actor='player',stage='draft')
        assert page.locator('[data-choose]').count() == 3
        shot('role-draft-1440')

        load('management/players')
        shot('management-1440')
        page.locator('[data-person="an"]').first.click()
        shot('player-drawer-1440')
        page.keyboard.press('Escape')
        load('management/requests')
        page.locator('#review-request').click()
        shot('request-drawer-1440')
        page.locator('#save-review').click()
        assert 'Đã chấp nhận' in page.locator('#center').inner_text()
        checks.append({'case':'player-and-request-drawer','result':'pass'})

        load('chat/vote')
        page.locator('#open-vote').click()
        page.locator('#vote-mode').select_option('secret')
        page.locator('#confirm-open-vote').click()
        page.locator('#actor').select_option('player')
        assert page.locator('#center .hint').count() == 0
        assert 'BÍ MẬT' in page.locator('#center').inner_text()
        shot('vote-secret-1440')
        # New fixture uses public mode; selecting a target alone creates no activity.
        load('chat/vote',actor='player')
        before=page.locator('#timeline').inner_text()
        page.locator('#ballot-choice').select_option('Bình')
        assert page.locator('#timeline').inner_text() == before
        page.locator('#ballot-form .primary').click()
        assert 'An vote Bình' in page.locator('#timeline').inner_text()
        shot('vote-public-1440')
        checks.append({'case':'secret-own-only-public-explicit-submit-mock','result':'pass'})

        load('chat/announcements')
        assert page.locator('#compose-text').count() == 1
        page.locator('#compose-text').fill('Nội dung hẹn thử')
        page.locator('#timing').select_option('next')
        page.locator('#composer button[type="submit"]').click()
        assert 'Nội dung hẹn thử' not in page.locator('#timeline').inner_text()
        shot('announcement-1440')
        checks.append({'case':'announcement-simple-composer-schedule-not-public','result':'pass'})
        load('chat/general',actor='player',stage='finished')
        assert page.locator('#compose-text').count() == 0
        load('management/history',actor='player',stage='finished')
        shot('finished-1440')
        checks.append({'case':'finished-readonly-and-history','result':'pass'})
        for state in ('loading','empty','offline'):
            page.goto(f'{URL}?state={state}#chat/general')
            page.wait_for_selector('#timeline')
            shot(f'chat-{state}-1440')
        checks.append({'case':'loading-empty-offline-design-states','result':'pass'})
        page.set_viewport_size({'width':390,'height':844})
        load('role/catalog',stage='lobby')
        page.locator('[data-edit-role="0"]').click()
        assert page.locator('.dialog-footer').bounding_box()['y'] < 844
        shot('role-editor-390')
        page.keyboard.press('Escape')
        load('management/players')
        page.locator('[data-person="an"]').first.click()
        shot('player-drawer-390')
        page.keyboard.press('Escape')
        checks.append({'case':'mobile-role-modal-player-drawer','result':'pass'})
        assert not errors, errors
        browser.close()
    result={'scope':'UX1 offline prototype only; not application acceptance E/F/G',
            'checks':checks, 'page_errors':errors}
    (ROOT / 'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'checks_passed':len(checks),'page_errors':errors},ensure_ascii=False))

if __name__=='__main__':
    main()
