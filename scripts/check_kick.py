"""Browser smoke check for moderator kick and the removed player's redirect."""

import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("KICK_API_ORIGIN", "http://127.0.0.1:8015")
WEB = os.environ.get("KICK_WEB_ORIGIN", "http://127.0.0.1:4175")
RESULT = ROOT / "docs/step-9/kick-browser.json"


def post(client: httpx.Client, path: str, body: dict, status: int = 200) -> dict:
    response = client.post(path, json=body, headers={"Origin": WEB})
    assert response.status_code == status, (path, response.status_code, response.text[:400])
    return response.json()


def context_for(browser, client: httpx.Client):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([{"name": "ma_soi_session", "value": client.cookies["ma_soi_session"],
                          "domain": "127.0.0.1", "path": "/", "httpOnly": True}])
    return context


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    assert url.split("/")[-1].split("?")[0] == "ma_soi_kick_review"
    actors = [httpx.Client(base_url=API, timeout=10) for _ in range(3)]
    room_id = None
    ids: list[str] = []
    try:
        host, target, other = actors
        for client, name in zip(actors, ("Kick browser host", "Kick browser target", "Kick browser other")):
            ids.append(post(client, "/api/auth/guest", {"display_name": name})["id"])
        room = post(host, "/api/rooms", {"name": "Thử kick trên web", "seat_limit": 2}, 201)
        room_id = room["id"]
        for client in (target, other):
            post(client, "/api/rooms/join", {
                "code": room["code"], "accept_postgame_transcript": True})

        with sync_playwright() as playwright:
            options = {"headless": True, "args": ["--no-sandbox"]}
            if os.environ.get("KICK_CHROMIUM_PATH"):
                options["executable_path"] = os.environ["KICK_CHROMIUM_PATH"]
            browser = playwright.chromium.launch(**options)
            errors: list[str] = []
            host_page = context_for(browser, host).new_page()
            target_page = context_for(browser, target).new_page()
            for page in (host_page, target_page):
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{WEB}/#lobby", wait_until="domcontentloaded")
                page.locator("#lobby-title").wait_for(timeout=15000)
            assert target_page.get_by_role("button", name="Kick").count() == 0

            seat = host_page.locator(".seat", has_text="Kick browser target")
            seat.get_by_role("button", name="Kick").click()
            host_page.locator("dialog[open]").get_by_label("Lý do kick").fill("Quấy rối sảnh")
            host_page.locator("dialog[open]").get_by_role("button", name="Tiếp tục").click()
            host_page.locator("dialog[open]").get_by_role("button", name="Xác nhận kick").click()
            host_page.locator(".seat", has_text="Kick browser target").wait_for(state="detached", timeout=15000)
            assert host_page.locator(".seat.empty").count() == 1
            target_page.bring_to_front()
            target_page.locator("#page-title").wait_for(timeout=20000)
            assert target_page.get_by_text("Bạn không còn ở phòng này").count() == 1
            target_page.get_by_label("Tên phòng").fill("Phòng sau khi bị kick")
            assert target_page.get_by_role("button", name="Tạo sảnh").is_enabled()
            assert not errors, errors
            result = {"host_kick_button": True, "non_host_cannot_kick": True,
                      "seat_released": True, "target_redirected_home": True,
                      "target_can_create_room": True, "page_errors": errors}
            RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            browser.close()
    finally:
        for actor in actors:
            actor.close()
        if room_id:
            with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM rooms WHERE id = %s", (UUID(room_id),))
                    cursor.execute("DELETE FROM users WHERE id = ANY(%s)",
                                   ([UUID(item) for item in ids],))


if __name__ == "__main__":
    main()
