"""Browser smoke check: a night death is visible only after dawn."""

import os
from uuid import UUID, uuid4

import httpx
import psycopg
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8017"
WEB = "http://127.0.0.1:4177"
URL = os.environ["DATABASE_URL"]
assert URL.rsplit("/", 1)[-1] == "ma_soi_schedule_review"


def post(client, path, body, status=200):
    result = client.post(path, json=body, headers={"Origin": WEB})
    assert result.status_code == status, (path, result.status_code, result.text[:400])
    return result.json()


def page_for(browser, client):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([{"name": "ma_soi_session", "value": client.cookies["ma_soi_session"],
                          "domain": "127.0.0.1", "path": "/", "httpOnly": True}])
    return context.new_page()


def main():
    host, victim, witness = [httpx.Client(base_url=API, timeout=10) for _ in range(3)]
    room_id = None
    ids = []
    try:
        for client, name in ((host, "Quản trò UI"), (victim, "Nạn nhân UI"), (witness, "Nhân chứng UI")):
            ids.append(post(client, "/api/auth/guest", {"display_name": name})["id"])
        room = post(host, "/api/rooms", {"name": "Đêm hẹn công bố", "seat_limit": 2}, 201)
        room_id = room["id"]
        base = f"/api/rooms/{room_id}"
        for client in (victim, witness):
            post(client, "/api/rooms/join", {"code": room["code"], "accept_postgame_transcript": True})
        post(host, f"{base}/roles", {"name": "Dân", "description": "Mô tả", "faction_label": "Làng",
            "seer_result": "Thiện", "role_kind": "passive", "quantity": 2}, 201)
        changed = host.patch(f"{base}/settings", json={"name": "Đêm hẹn công bố",
            "seat_limit": 2, "registration_open": False}, headers={"Origin": WEB})
        assert changed.status_code == 200
        post(host, f"{base}/roles/distribute-random", {})
        post(host, f"{base}/game/start", {})
        post(host, f"{base}/game/advance", {"client_request_id": str(uuid4()),
            "expected_phase_type": "day", "expected_round_number": 0})

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True,
                executable_path="/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome",
                args=["--no-sandbox"])
            host_page = page_for(browser, host)
            witness_page = page_for(browser, witness)
            errors = []
            for page in (host_page, witness_page):
                page.on("pageerror", lambda error: errors.append(str(error)))
            host_page.goto(f"{WEB}/#control")
            host_page.locator("#host-board-title").wait_for()
            witness_page.goto(f"{WEB}/#dead")
            witness_page.locator("#death-notices-title").wait_for()

            host_page.get_by_role("button", name="Thao tác").first.click()
            board_dialog = host_page.locator(".player-dialog[open]")
            board_dialog.get_by_role("button", name="Giết").click()
            host_page.locator(".confirmation-dialog[open]").get_by_role("button", name="Đánh dấu chết").click()
            board_dialog.get_by_text("Xử lý thông báo người chết").wait_for()
            board_dialog.get_by_label("Nguyên nhân công khai (tùy chọn)").fill("Bị sói tấn công")
            board_dialog.get_by_label("Thời điểm công bố").select_option("next_phase")
            board_dialog.get_by_role("button", name="Hẹn thông báo").click()
            host_page.locator(".confirmation-dialog[open]").get_by_role("button", name="Hẹn thông báo").click()
            board_dialog.get_by_text("đã hẹn chuyển pha").wait_for()
            assert witness_page.get_by_text("Bị sói tấn công").count() == 0
            public = witness.get(base).json()
            assert next(member for member in public["members"] if member["user_id"] == ids[1])["public_is_alive"]
            board_dialog.get_by_role("button", name="Đóng").click()

            host_page.get_by_role("button", name="Thông báo").click()
            host_page.get_by_text("Đã hẹn khi chuyển pha (1)").wait_for()
            host_page.get_by_text("Sang Ngày 1").wait_for()
            host_page.get_by_role("button", name="Trận đấu").click()
            host_page.get_by_role("button", name="Chuyển sang Ngày 1").click()
            confirmation = host_page.locator(".confirmation-dialog[open]")
            assert "1 thông báo đã hẹn" in confirmation.inner_text()
            confirmation.get_by_role("button", name="Chuyển sang Ngày 1").click()
            host_page.locator("#game-title").get_by_text("Ngày 1").wait_for()
            witness_page.get_by_text("Bị sói tấn công").wait_for(timeout=5000)
            public = witness.get(base).json()
            assert not next(member for member in public["members"] if member["user_id"] == ids[1])["public_is_alive"]

            host_page.get_by_role("button", name="Thông báo").click()
            host_page.get_by_role("button", name="Đăng thông báo").click()
            form = host_page.locator(".announcement-dialog[open]")
            form.get_by_label("Nội dung mọi thành viên sẽ thấy").fill("Sẽ hủy trước đêm")
            form.get_by_label("Thời điểm công bố").select_option("next_phase")
            form.get_by_role("button", name="Xem trước và hẹn").click()
            host_page.locator(".confirmation-dialog[open]").get_by_role("button", name="Hẹn thông báo").click()
            form.wait_for(state="hidden")
            host_page.get_by_text("Sẽ hủy trước đêm").wait_for()
            assert witness_page.get_by_text("Sẽ hủy trước đêm").count() == 0
            host_page.get_by_role("button", name="Hủy lịch").click()
            host_page.locator(".confirmation-dialog[open]").get_by_role("button", name="Hủy lịch").click()
            host_page.get_by_text("Sẽ hủy trước đêm").wait_for(state="detached")
            assert not errors, errors
            print("Browser OK: night death stays private until dawn and appears live; scheduled custom notice can be cancelled")
            browser.close()
    finally:
        for client in (host, victim, witness):
            client.close()
        if room_id:
            with psycopg.connect(URL.replace("postgresql+psycopg://", "postgresql://")) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM rooms WHERE id = %s", (UUID(room_id),))
                    cursor.execute("DELETE FROM users WHERE id = ANY(%s)", ([UUID(value) for value in ids],))


if __name__ == "__main__":
    main()
