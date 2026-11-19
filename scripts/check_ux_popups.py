"""Browser smoke check for moderator popups and reason-free actions."""

import os
from uuid import UUID

import httpx
import psycopg
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8016"
WEB = "http://127.0.0.1:4176"
URL = os.environ["DATABASE_URL"]
assert URL.rsplit("/", 1)[-1] == "ma_soi_ux_review"


def post(client, path, body, status=200):
    response = client.post(path, json=body, headers={"Origin": WEB})
    assert response.status_code == status, (path, response.status_code, response.text[:400])
    return response.json()


def main():
    host, player, other = [httpx.Client(base_url=API, timeout=10) for _ in range(3)]
    room_id = None
    user_ids = []
    try:
        for client, name in ((host, "Host UI"), (player, "Player UI"), (other, "Other UI")):
            user_ids.append(post(client, "/api/auth/guest", {"display_name": name})["id"])
        room = post(host, "/api/rooms", {"name": "Kiểm tra popup", "seat_limit": 2}, 201)
        room_id = room["id"]
        base = f"/api/rooms/{room_id}"
        post(player, "/api/rooms/join", {"code": room["code"], "accept_postgame_transcript": True})
        post(other, "/api/rooms/join", {"code": room["code"], "accept_postgame_transcript": True})

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True,
                executable_path="/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome",
                args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_cookies([{"name": "ma_soi_session", "value": host.cookies["ma_soi_session"],
                                  "domain": "127.0.0.1", "path": "/", "httpOnly": True}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{WEB}/#lobby")
            page.locator("#lobby-title").wait_for()
            page.get_by_role("button", name="Kick").first.click()
            dialog = page.locator(".confirmation-dialog[open]")
            textarea = dialog.get_by_label("Lý do kick")
            label_box, text_box = dialog.locator("label").bounding_box(), textarea.bounding_box()
            assert label_box and text_box and text_box["width"] > 280
            assert text_box["y"] > label_box["y"] + 15
            assert text_box["x"] >= label_box["x"]
            dialog.get_by_role("button", name="Hủy").click()

            page.goto(f"{WEB}/#chat")
            page.get_by_role("button", name="Cài đặt chat").click()
            settings = page.locator(".settings-dialog[open]")
            assert settings.get_by_text("Chính sách chat của phòng").is_visible()
            settings.get_by_label("Cho gửi chat chung ban đêm").check()
            settings.get_by_role("button", name="Lưu chính sách").click()
            page.locator(".confirmation-dialog[open]").get_by_role("button", name="Lưu quyền chat").click()
            settings.get_by_label("Cho gửi chat chung ban đêm").wait_for()
            settings.get_by_role("button", name="Đóng cài đặt chat").click()

            post(host, f"{base}/roles", {"name": "Dân thử", "description": "Thử nghiệm",
                "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "passive", "quantity": 2}, 201)
            changed = host.patch(f"{base}/settings", json={"name": "Kiểm tra popup",
                "seat_limit": 2, "registration_open": False}, headers={"Origin": WEB})
            assert changed.status_code == 200, changed.text
            post(host, f"{base}/roles/distribute-random", {})
            post(host, f"{base}/game/start", {})
            page.goto(f"{WEB}/?refresh=1#control")
            page.locator("#host-board-title").wait_for()
            assert page.get_by_role("button", name="Phân role").count() == 0
            page.get_by_role("button", name="Thao tác").first.click()
            player_dialog = page.locator(".player-dialog[open]")
            assert player_dialog.get_by_role("button", name="Giết").is_enabled()
            player_dialog.get_by_role("button", name="Giết").click()
            confirm = page.locator(".confirmation-dialog[open]")
            confirm.get_by_role("button", name="Đánh dấu chết").click()
            player_dialog.get_by_text("Đã chết").first.wait_for()
            player_dialog.get_by_role("button", name="Đóng").click()

            page.goto(f"{WEB}/#dead")
            page.get_by_role("button", name="Đăng thông báo").click()
            announcement = page.locator(".announcement-dialog[open]")
            assert announcement.get_by_label("Nội dung mọi thành viên sẽ thấy").is_visible()
            announcement.get_by_label("Nội dung mọi thành viên sẽ thấy").fill("Thông báo từ popup")
            announcement.get_by_role("button", name="Xem trước và đăng").click()
            page.locator(".confirmation-dialog[open]").get_by_role("button", name="Đăng thông báo").click()
            announcement.wait_for(state="hidden")
            page.get_by_text("Thông báo từ popup").wait_for()
            assert not errors, errors
            print("Browser OK: Kick layout 390px, chat/announcement dialog submissions, hidden host role tab, kill without reason")
            browser.close()
    finally:
        host.close()
        player.close()
        other.close()
        if room_id:
            with psycopg.connect(URL.replace("postgresql+psycopg://", "postgresql://")) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM rooms WHERE id = %s", (UUID(room_id),))
                    cursor.execute("DELETE FROM users WHERE id = ANY(%s)", ([UUID(value) for value in user_ids],))


if __name__ == "__main__":
    main()
