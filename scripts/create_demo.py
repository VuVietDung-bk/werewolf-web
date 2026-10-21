"""Seed a local room and switchable demo accounts through the regular HTTP API."""

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".local-demo.json"
API = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:5173"
EXTRA_ROLES = [
    {"name": "Phù thủy", "description": "Ban đêm có thể đề nghị cứu hoặc đầu độc. Quản trò xác nhận giới hạn dùng và kết quả.",
     "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "active", "request_phases": ["night"], "icon_key": "potion"},
    {"name": "Sói Băng", "description": "Ban đêm có thể đề nghị đóng băng một người. Quản trò quyết định hiệu ứng.",
     "faction_label": "Sói", "seer_result": "Ác", "team_chat_label": "Sói", "role_kind": "active", "request_phases": ["night"], "icon_key": "wolf"},
    {"name": "Kẻ Sống Sót", "description": "Cố gắng sống đến cuối ván theo luật do quản trò đặt.",
     "faction_label": "Trung lập", "seer_result": "Không rõ", "role_kind": "passive", "icon_key": "neutral"},
    {"name": "Bảo vệ", "description": "Ban đêm có thể đề nghị bảo vệ một người. Quản trò xác nhận kết quả.",
     "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "active", "request_phases": ["night"]},
    {"name": "Người nổi tiếng", "description": "Quản trò quyết định khi nào công bố danh tính sau khi người này chết.",
     "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "passive"},
    {"name": "Kẻ Phóng Hỏa", "description": "Ban đêm có thể đề nghị đánh dấu mục tiêu theo luật phòng.",
     "faction_label": "Trung lập", "seer_result": "Không rõ", "role_kind": "active", "request_phases": ["night"], "icon_key": "neutral"},
    {"name": "Thợ rèn", "description": "Ban ngày có thể đề nghị tạo một vật phẩm theo luật phòng.",
     "faction_label": "Làng", "seer_result": "Thiện", "role_kind": "active", "request_phases": ["day"]},
    {"name": "Sói Pháp Sư", "description": "Ban đêm có thể đề nghị dùng phép theo luật phòng.",
     "faction_label": "Sói", "seer_result": "Ác", "team_chat_label": "Sói", "role_kind": "active", "request_phases": ["night"], "icon_key": "wolf"},
]


def checked(response: httpx.Response) -> dict:
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"API {response.status_code}: {detail}")
    return response.json()


def post(client: httpx.Client, path: str, body: dict | None = None) -> dict:
    return checked(client.post(path, json=body, headers={"Origin": ORIGIN}))


def save_manifest(value: dict) -> None:
    temporary = MANIFEST.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, MANIFEST)
    finally:
        temporary.unlink(missing_ok=True)


def create(players: int) -> dict:
    with httpx.Client(base_url=API, timeout=10, trust_env=False) as host:
        checked(host.get("/api/health/live"))
        availability = host.get("/api/dev/demo", headers={"X-Demo-Key": "probe"})
        if availability.status_code == 404 and availability.json().get("detail") == "Chế độ trải nghiệm chưa bật":
            raise RuntimeError("API cần chạy với APP_ENV=local và ENABLE_DEMO_MODE=1")
        host_id = post(host, "/api/auth/guest", {"display_name": "Quản trò thử"})["id"]
        room = post(host, "/api/rooms", {"name": "Phòng trải nghiệm Ma Sói", "seat_limit": players})
        actors = [{"id": host_id, "name": "Quản trò thử", "kind": "host", "seat": None}]
        for seat in range(1, players + 1):
            with httpx.Client(base_url=API, timeout=10, trust_env=False) as player:
                name = f"Người chơi thử {seat}"
                user_id = post(player, "/api/auth/guest", {"display_name": name})["id"]
                post(player, "/api/rooms/join", {
                    "code": room["code"], "accept_postgame_transcript": True})
                actors.append({"id": user_id, "name": name, "kind": "player", "seat": seat})

        base = f"/api/rooms/{room['id']}"
        post(host, f"{base}/role-set/sample")
        for role in EXTRA_ROLES[:max(0, players - 4)]:
            post(host, f"{base}/roles", {**role, "quantity": 1})
        catalog = checked(host.get(f"{base}/role-catalog"))
        quantities = {"Dân làng": 1, "Tiên Tri": int(players >= 3),
                      "Sói thường": 1, "Xạ thủ": int(players >= 4)}
        for role in catalog["roles"]:
            if role["name"] not in quantities:
                continue
            checked(host.patch(f"{base}/roles/{role['id']}/quantity",
                               json={"quantity": quantities[role["name"]]},
                               headers={"Origin": ORIGIN}))
        return {"key": secrets.token_urlsafe(32), "room_id": room["id"], "actors": actors}


def main() -> int:
    global API, ORIGIN
    parser = argparse.ArgumentParser(description="Tạo phòng và tài khoản ảo để tự thử web local")
    parser.add_argument("--players", type=int, default=5, help="Số người chơi ảo, từ 2 đến 12")
    parser.add_argument("--api-port", type=int, default=8000, help="Cổng API local")
    parser.add_argument("--web-port", type=int, default=5173, help="Cổng Vite local")
    parser.add_argument("--new", action="store_true", help="Tạo phòng mẫu mới, giữ phòng mẫu cũ trong DB")
    args = parser.parse_args()
    if not 2 <= args.players <= 12:
        parser.error("--players phải từ 2 đến 12")
    if not 1 <= args.api_port <= 65535 or not 1 <= args.web_port <= 65535:
        parser.error("Cổng phải từ 1 đến 65535")
    API = f"http://127.0.0.1:{args.api_port}"
    ORIGIN = f"http://localhost:{args.web_port}"
    try:
        if MANIFEST.exists() and not args.new:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            print("Dùng lại phòng trải nghiệm hiện có. Thêm --new nếu muốn tạo phòng mới.")
        else:
            manifest = create(args.players)
            save_manifest(manifest)
            print(f"Đã tạo phòng mẫu với {args.players} người chơi ảo và 1 quản trò.")
    except (OSError, ValueError, KeyError, httpx.HTTPError, RuntimeError) as error:
        print(f"Không tạo được phòng trải nghiệm: {error}", file=sys.stderr)
        return 1
    print(f"Mở: {ORIGIN}/#demo={manifest['key']}")
    print("Chọn tài khoản ở thanh 'Trải nghiệm local'. Phòng mẫu có ít nhất một loại role riêng cho mỗi người, chưa phát role.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
