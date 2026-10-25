"""Browser check for one moderator conversation and private team chat."""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("CHAT_API_ORIGIN", "http://127.0.0.1:8013")
WEB = "http://127.0.0.1:4173"
OUT = ROOT / "docs/step-6/screenshots"


def post(client, path, body, status=200):
    response = client.post(path, json=body, headers={"Origin": WEB})
    assert response.status_code == status, (path, response.status_code, response.text[:500])
    return response.json() if response.content else None


def command(**values):
    return {"client_request_id": str(uuid4()), **values}


def context_for(browser, client):
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    context.add_cookies([{"name": "ma_soi_session", "value": client.cookies["ma_soi_session"],
                          "domain": "127.0.0.1", "path": "/", "httpOnly": True}])
    return context


def main():
    url = os.environ.get("DATABASE_URL", "")
    if url.split("/")[-1].split("?")[0] != "ma_soi_chat_review":
        raise SystemExit("DATABASE_URL must name ma_soi_chat_review")
    OUT.mkdir(parents=True, exist_ok=True)
    actors = [httpx.Client(base_url=API, timeout=10) for _ in range(4)]
    room_id = None
    user_ids = []
    try:
        for actor, name in zip(actors, ("Quản trò chat", "Sói A", "Sói B", "Dân bị động")):
            user_ids.append(post(actor, "/api/auth/guest", {"display_name": name})["id"])
        host, first, second, third = actors
        room = post(host, "/api/rooms", {"name": "Thử chat chung một chỗ", "seat_limit": 3}, 201)
        room_id = room["id"]
        base = f"/api/rooms/{room_id}"
        for actor in actors[1:]:
            post(actor, "/api/rooms/join", {"code": room["code"], "accept_postgame_transcript": True})
        post(host, f"{base}/roles", {"name": "Sói thử chat", "description": "Chat đội vào đêm",
            "faction_label": "Sói", "seer_result": "Ác", "team_chat_label": "Bầy sói",
            "role_kind": "active", "request_phases": ["night"], "quantity": 2,
            "host_contact": {"can_initiate": True, "initiate_phases": ["night"],
                             "reply_allowed": True, "when_dead": False}}, 201)
        post(host, f"{base}/roles", {"name": "Dân bị động", "description": "Không tự nhắn quản trò",
            "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "passive", "quantity": 1,
            "host_contact": {"can_initiate": False, "initiate_phases": [],
                             "reply_allowed": False, "when_dead": False}}, 201)
        response = host.patch(f"{base}/settings", json={"name": room["name"], "seat_limit": 3,
            "registration_open": False}, headers={"Origin": WEB})
        assert response.status_code == 200
        post(host, f"{base}/roles/distribute-random", {})
        assignments = host.get(f"{base}/roles/assignments").json()
        by_id = dict(zip(user_ids[1:], actors[1:]))
        wolves = [item["user_id"] for item in assignments if item["role"]["team_chat_label"]]
        villager = next(item["user_id"] for item in assignments if not item["role"]["team_chat_label"])
        post(host, f"{base}/game/start", {})
        post(host, f"{base}/chat/messages", command(channel="host", participant_user_id=villager,
            text="Quản trò mở hội thoại với bạn"))
        post(host, f"{base}/game/players/{villager}/private-messages", command(
            text="Thông điệp một chiều từ bàn quản trò"))
        assert by_id[wolves[0]].get(f"{base}/game/private-messages").json() == []
        assert by_id[villager].get(f"{base}/game/private-messages").json()[0]["sequence"] > 0
        post(host, f"{base}/game/advance", {"expected_phase_type": "day", "expected_round_number": 0})
        post(by_id[wolves[0]], f"{base}/chat/messages", command(channel="team", text="Bầy sói họp đêm 1"))
        assert [item["text"] for item in by_id[wolves[1]].get(
            f"{base}/chat/messages?channel=team").json()["messages"]] == ["Bầy sói họp đêm 1"]
        assert by_id[villager].get(f"{base}/chat/messages?channel=team").json()["messages"] == []

        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox"]}
            if os.environ.get("CHAT_CHROMIUM_PATH"):
                launch["executable_path"] = os.environ["CHAT_CHROMIUM_PATH"]
            browser = playwright.chromium.launch(**launch)
            errors = []
            citizen = context_for(browser, by_id[villager])
            page = citizen.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{WEB}/#private", wait_until="domcontentloaded")
            page.locator("#chat-title").wait_for(timeout=15000)
            assert page.url.endswith("#chat")
            assert page.locator(".screen-nav button", has_text="Tin riêng").count() == 0
            page.locator(".chat-channel-tabs button").filter(has_text="Quản trò").click()
            page.get_by_text("Thông điệp một chiều từ bàn quản trò").wait_for(timeout=15000)
            page.get_by_text("Quản trò mở hội thoại với bạn").wait_for(timeout=15000)
            lines = page.locator(".chat-message-list > li").all_inner_texts()
            assert len(lines) == 2 and "Quản trò mở hội thoại" in lines[0]
            assert "Thông điệp một chiều" in lines[1]
            assert page.locator(".chat-send").count() == 0
            assert page.locator(".chat-channel-tabs button").filter(has_text="Đội").count() == 0
            page.locator(".chat-channel-tabs button").filter(has_text="Chung").click()
            post(host, f"{base}/game/players/{villager}/private-messages", command(
                text="Tin mới khi đang mở Chat"))
            host_tab = page.locator(".chat-channel-tabs button").filter(has_text="Quản trò")
            host_tab.locator(".chat-unread").wait_for(timeout=15000)
            host_tab.click()
            page.get_by_text("Tin mới khi đang mở Chat").wait_for(timeout=15000)
            page.screenshot(path=str(OUT / "chat-host-unified-mobile.png"), full_page=True)
            citizen.close()

            wolf = context_for(browser, by_id[wolves[1]])
            page = wolf.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{WEB}/#chat", wait_until="domcontentloaded")
            page.locator("#chat-title").wait_for(timeout=15000)
            page.locator(".chat-channel-tabs button").filter(has_text="Đội").click()
            page.get_by_text("Bầy sói họp đêm 1").wait_for(timeout=15000)
            assert page.locator(".chat-send").count() == 1
            assert page.get_by_text("Thông điệp một chiều từ bàn quản trò").count() == 0
            page.screenshot(path=str(OUT / "chat-team-night-mobile.png"), full_page=True)
            wolf.close()
            browser.close()
            assert not errors, errors

        result = {"room_id": room_id, "team_members": 2, "villager_cannot_reply": True,
                  "private_message_in_host_timeline": True, "team_message_private": True,
                  "old_private_hash_redirects_to_chat": True, "new_private_unread_visible": True,
                  "page_errors": errors}
        (ROOT / "docs/step-6/unified-chat-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        for actor in actors:
            actor.close()
        if room_id:
            with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1)) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM rooms WHERE id = %s", (room_id,))
                    for user_id in user_ids:
                        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))


if __name__ == "__main__":
    main()
