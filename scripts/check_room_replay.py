"""Browser smoke check: finish, open next lobby, carry player, then leave."""

import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("REPLAY_API_ORIGIN", "http://127.0.0.1:8014")
WEB = os.environ.get("REPLAY_WEB_ORIGIN", "http://127.0.0.1:4174")
RESULT = ROOT / "docs/step-9/room-replay-browser.json"


def post(client: httpx.Client, path: str, body: dict | None = None, status: int = 200):
    response = client.post(path, json=body, headers={"Origin": WEB})
    assert response.status_code == status, (path, response.status_code, response.text[:500])
    return response.json() if response.content else None


def browser_context(browser, client: httpx.Client):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([{"name": "ma_soi_session", "value": client.cookies["ma_soi_session"],
                          "domain": "127.0.0.1", "path": "/", "httpOnly": True}])
    return context


def main():
    url = os.environ.get("DATABASE_URL", "")
    assert url.split("/")[-1].split("?")[0] == "ma_soi_replay_review"
    actors = [httpx.Client(base_url=API, timeout=10) for _ in range(3)]
    room_id = None
    user_ids = []
    try:
        for client, name in zip(actors, ("Quản trò ván mới", "Người chơi A", "Người chơi B")):
            user_ids.append(post(client, "/api/auth/guest", {"display_name": name})["id"])
        host, first, second = actors
        room = post(host, "/api/rooms", {"name": "Làng chơi tiếp", "seat_limit": 2}, 201)
        room_id = room["id"]
        base = f"/api/rooms/{room_id}"
        for player in (first, second):
            post(player, "/api/rooms/join", {
                "code": room["code"], "accept_postgame_transcript": True})
        post(host, f"{base}/roles", {"name": "Dân làng", "description": "Thử ván mới",
             "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "passive", "quantity": 2}, 201)
        response = host.patch(f"{base}/settings", json={"name": room["name"],
            "seat_limit": 2, "registration_open": False}, headers={"Origin": WEB})
        assert response.status_code == 200
        post(host, f"{base}/roles/distribute-random")
        post(host, f"{base}/game/start")
        post(host, f"{base}/game/finish", {
            "result_label": "Làng thắng", "result_reason": "Quản trò kết thúc thử"})

        with sync_playwright() as playwright:
            options = {"headless": True, "args": ["--no-sandbox"]}
            if os.environ.get("REPLAY_CHROMIUM_PATH"):
                options["executable_path"] = os.environ["REPLAY_CHROMIUM_PATH"]
            browser = playwright.chromium.launch(**options)
            errors = []
            host_context = browser_context(browser, host)
            player_context = browser_context(browser, first)
            host_page = host_context.new_page()
            player_page = player_context.new_page()
            for page in (host_page, player_page):
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{WEB}/#game", wait_until="domcontentloaded")
                page.locator("#game-title").wait_for(timeout=15000)

            player_page.get_by_role("button", name="Rời trận").wait_for()
            host_page.get_by_role("button", name="Bắt đầu trận mới trong sảnh").click()
            host_page.locator("dialog[open]").get_by_role("button", name="Bắt đầu ván mới").click()
            host_page.locator("#lobby-title").wait_for(timeout=15000)
            next_room = host.get("/api/rooms/mine").json()[0]
            assert next_room["id"] != room_id and next_room["code"] == room["code"]
            assert host_page.get_by_text(room["code"]).count() > 0
            host_page.get_by_role("button", name="Trang chủ").click()
            host_page.locator("#page-title").wait_for()
            host_page.reload()
            host_page.locator("#page-title").wait_for()
            host_page.locator(".room-series").wait_for(timeout=15000)
            assert host_page.locator(".room-series").count() == 1
            assert host_page.locator(".room-series > .room-tab").count() == 1
            assert host_page.locator(".room-series > .room-tab").inner_text().count("Làng chơi tiếp") == 1
            assert "Ván 2 · Sảnh chờ" in host_page.locator(".room-series > .room-tab").inner_text()
            host_page.locator(".room-history summary").click()
            host_page.locator(".room-history-list").get_by_role("button", name="Ván 1").click()
            host_page.locator("#transcript-title").wait_for(timeout=15000)
            host_page.get_by_role("button", name="Vào ván hiện tại").click()
            host_page.locator("#lobby-title").wait_for(timeout=15000)
            player_page.bring_to_front()
            player_page.get_by_role("button", name="Vào ván hiện tại").first.wait_for(timeout=20000)
            player_page.get_by_role("button", name="Vào ván hiện tại").first.click()
            player_page.locator("#lobby-title").wait_for(timeout=15000)
            assert player_page.get_by_text(room["code"]).count() > 0
            player_page.get_by_role("button", name="Rời phòng").click()
            player_page.locator("#page-title").wait_for(timeout=15000)
            assert room_id in [item["id"] for item in first.get("/api/rooms/mine").json()]
            post(first, "/api/rooms/join", {
                "code": room["code"], "accept_postgame_transcript": True})
            next_base = f"/api/rooms/{next_room['id']}"
            response = host.patch(f"{next_base}/settings", json={"name": room["name"],
                "seat_limit": 2, "registration_open": False}, headers={"Origin": WEB})
            assert response.status_code == 200
            post(host, f"{next_base}/roles/distribute-random")
            post(host, f"{next_base}/game/start")
            post(host, f"{next_base}/game/finish", {
                "result_label": "Ván hai", "result_reason": "Kiểm tra lịch sử nhiều ván"})
            third = post(host, f"{next_base}/game/next")
            assert third["id"] != next_room["id"]
            host_page.goto(f"{WEB}/?series=3#home")
            host_page.locator(".room-series").wait_for(timeout=15000)
            assert host_page.locator(".room-series").count() == 1
            assert "Ván 3 · Sảnh chờ" in host_page.locator(".room-series > .room-tab").inner_text()
            assert "Lịch sử (2)" in host_page.locator(".room-history summary").inner_text()
            if not host_page.locator(".room-history-list").is_visible():
                host_page.locator(".room-history summary").click()
            assert host_page.locator(".room-history-list .room-tab").count() == 2
            host_page.locator(".room-history-list").get_by_role("button", name="Ván 1").click()
            host_page.locator("#transcript-title").wait_for(timeout=15000)
            host_page.get_by_role("button", name="Vào ván hiện tại").click()
            host_page.locator("#lobby-title").wait_for(timeout=15000)
            assert third["id"] == host.get("/api/rooms/mine").json()[0]["id"]
            assert not errors, errors
            result = {"same_invite_code": True, "player_sees_next_lobby": True,
                      "one_room_with_history_menu": True, "three_games_one_room_group": True,
                      "player_can_leave": True, "old_transcript_retained": True,
                      "page_errors": errors}
            RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            browser.close()
            print(json.dumps(result, ensure_ascii=False))
    finally:
        for actor in actors:
            actor.close()
        if room_id:
            sync_url = url.replace("postgresql+psycopg://", "postgresql://")
            with psycopg.connect(sync_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM rooms WHERE owner_user_id = %s", (UUID(user_ids[0]),))
                    cursor.execute("DELETE FROM users WHERE id = ANY(%s)",
                                   ([UUID(item) for item in user_ids],))


if __name__ == "__main__":
    main()
