"""Check a real sample game in desktop/mobile Chromium; API and Vite must be running."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/step-9/screenshots"
WEB = os.environ.get("D5_WEB_ORIGIN", "http://127.0.0.1:5173")
API = "http://127.0.0.1:8000"
ORIGIN = WEB


def post(client, path, body, status=200):
    response = client.post(path, json=body, headers={"Origin": ORIGIN})
    if response.status_code != status:
        raise AssertionError(f"POST {path}: {response.status_code} {response.text[:500]}")
    return response.json() if response.content else None


def command(**values):
    return {"client_request_id": str(uuid4()), **values}


def browser_context(browser, actor, width, height, **options):
    context = browser.new_context(viewport={"width": width, "height": height},
                                  device_scale_factor=1, **options)
    context.add_cookies([{"name": "ma_soi_session", "value": actor.cookies["ma_soi_session"],
                          "domain": "127.0.0.1", "path": "/", "httpOnly": True}])
    return context


def inspect(page):
    return page.evaluate('''() => ({
      heading: document.querySelector('h1')?.textContent,
      missingAlt: document.querySelectorAll('img:not([alt])').length,
      loadedImages: [...document.images].map(i => ({path: new URL(i.src).pathname, loaded: i.complete && i.naturalWidth > 0})),
      assetRequests: performance.getEntriesByType('resource').filter(x => x.name.includes('/assets/v1/')).map(x => new URL(x.name).pathname),
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth
    })''')


def main():
    url = os.environ.get("DATABASE_URL", "")
    if url.split("/")[-1].split("?")[0] != "ma_soi_checkpoint_d":
        raise SystemExit("DATABASE_URL must name ma_soi_checkpoint_d")
    OUT.mkdir(parents=True, exist_ok=True)
    host, first, second = [httpx.Client(base_url=API, timeout=10) for _ in range(3)]
    room_id = None
    user_ids = []
    try:
        for client, name in zip((host, first, second),
                                ("D5 quản trò mẫu", "D5 người chơi A", "D5 người chơi B")):
            user_ids.append(post(client, "/api/auth/guest", {"display_name": name})["id"])
        room = post(host, "/api/rooms", {"name": "Làng thử giao diện D5", "seat_limit": 2}, 201)
        room_id = room["id"]
        base = f"/api/rooms/{room_id}"
        for client in (first, second):
            post(client, "/api/rooms/join", {"code": room["code"],
                                             "accept_postgame_transcript": True})
        for name, faction, icon in (("Sói thử", "Sói", "wolf"),
                                    ("Tiên Tri thử", "Làng", "seer")):
            post(host, f"{base}/roles", {"name": name, "description": f"Mô tả {name}",
                "faction_label": faction, "seer_result": "Ác" if faction == "Sói" else "Thiện",
                "team_chat_label": "Bầy sói" if faction == "Sói" else None,
                "role_kind": "passive", "quantity": 1, "icon_key": icon}, 201)
        response = host.patch(f"{base}/settings", json={"name": room["name"], "seat_limit": 2,
            "registration_open": False}, headers={"Origin": ORIGIN})
        assert response.status_code == 200
        post(host, f"{base}/roles/distribute-random", {})
        post(host, f"{base}/game/start", {})
        post(host, f"{base}/game/advance", {"expected_phase_type": "day", "expected_round_number": 0})
        post(host, f"{base}/game/advance", {"expected_phase_type": "night", "expected_round_number": 1})
        post(first, f"{base}/chat/messages", command(channel="common", text="Tin chung của ván thử D5"))
        post(host, f"{base}/game/events/announcements", command(text="Bình minh đã đến."))

        with sync_playwright() as p:
            launch_options = {"headless": True, "args": ["--no-sandbox"]}
            if os.environ.get("D5_CHROMIUM_PATH"):
                launch_options["executable_path"] = os.environ["D5_CHROMIUM_PATH"]
            browser = p.chromium.launch(**launch_options)
            result = {"room_id": room_id, "screens": {}}
            errors = []
            desktop = browser_context(browser, host, 1280, 900)
            page = desktop.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{WEB}/#control", wait_until="domcontentloaded")
            page.locator("#host-board-title").wait_for(timeout=15000)
            page.screenshot(path=str(OUT / "d5-host-desktop.png"), full_page=True)
            result["screens"]["host_desktop"] = inspect(page)
            desktop.close()

            mobile = browser_context(browser, first, 390, 844, reduced_motion="reduce")
            page = mobile.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{WEB}/#game", wait_until="domcontentloaded")
            page.locator("#game-title").wait_for(timeout=15000)
            assert page.locator("#game-title").inner_text() == "Ngày 1"
            assert "Sói thử" not in page.locator(".seat-grid").inner_text()
            assert "Tiên Tri thử" not in page.locator(".seat-grid").inner_text()
            page.screenshot(path=str(OUT / "d5-player-mobile.png"), full_page=True)
            result["screens"]["player_mobile"] = inspect(page)
            page.get_by_role("button", name="Danh mục role").click()
            page.get_by_text("Sói thử").first.wait_for(timeout=15000)
            page.screenshot(path=str(OUT / "d5-catalog-mobile.png"), full_page=True)
            result["screens"]["catalog_mobile"] = inspect(page)
            page.get_by_role("button", name="Thông báo").click()
            page.get_by_text("Bình minh đã đến.").wait_for(timeout=15000)
            page.screenshot(path=str(OUT / "d5-notice-mobile.png"), full_page=True)
            result["screens"]["notice_mobile"] = inspect(page)
            mobile.close()

            missing = browser_context(browser, first, 390, 844)
            missing.route("**/assets/v1/*", lambda route: route.abort())
            page = missing.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{WEB}/#game", wait_until="domcontentloaded")
            page.locator("#game-title").wait_for(timeout=15000)
            page.screenshot(path=str(OUT / "d5-missing-art-mobile.png"), full_page=True)
            result["screens"]["missing_art"] = inspect(page)
            assert page.locator("#game-title").is_visible()
            page.get_by_role("button", name="Danh mục role").click()
            page.get_by_text("Sói thử").first.wait_for(timeout=15000)
            page.screenshot(path=str(OUT / "d5-missing-art-catalog-mobile.png"), full_page=True)
            result["screens"]["missing_art_catalog"] = inspect(page)
            missing.close()

            slow = browser_context(browser, first, 390, 844)
            page = slow.new_page()
            cdp = slow.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150,
                "downloadThroughput": 200000, "uploadThroughput": 100000})
            start = time.monotonic()
            page.goto(f"{WEB}/#game", wait_until="domcontentloaded", timeout=30000)
            page.locator("#game-title").wait_for(timeout=30000)
            result["slow_game_heading_ms"] = round((time.monotonic() - start) * 1000)
            page.wait_for_function("performance.getEntriesByType('resource').some(x => x.name.includes('village-day-v1.webp'))", timeout=30000)
            result["slow_game_art_ms"] = round((time.monotonic() - start) * 1000)
            result["screens"]["slow_game"] = inspect(page)
            slow.close()
            browser.close()
            result["page_errors"] = errors
            assert not errors
            assert all(not screen["horizontalOverflow"] and screen["missingAlt"] == 0
                       for screen in result["screens"].values())
            assert all("/api/rooms/" not in path for screen in result["screens"].values()
                       for path in screen["assetRequests"])
            (ROOT / "docs/step-9/checkpoint-d-visual.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        for client in (host, first, second):
            client.close()
        if room_id:
            with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1)) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM rooms WHERE id = %s", (room_id,))
                    for user_id in user_ids:
                        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))


if __name__ == "__main__":
    main()
