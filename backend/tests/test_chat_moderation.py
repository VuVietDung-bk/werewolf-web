"""Rate limiting, literal text, and host moderation across chat channels."""

import asyncio
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app import chat
from app.db.models import GameEvent, Outbox, Room, User
from app.db.session import session_factory
from app.main import app
from app.live import may_receive

ORIGIN = "http://localhost:5173"


async def post(client, path, body=None):
    return await client.post(path, json=body, headers={"Origin": ORIGIN})


def message(channel, text, **extra):
    return {"client_request_id": str(uuid4()), "channel": channel, "text": text, **extra}


def test_rate_limit_literal_text_and_host_mute(monkeypatch) -> None:
    monkeypatch.setattr(chat, "CHAT_BURST_LIMIT", 2)
    monkeypatch.setattr(chat, "CHAT_MINUTE_LIMIT", 100)

    async def run() -> None:
        ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, first, second = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(3)]
            try:
                for client, name in ((host, "Mute host"), (first, "Mute A"), (second, "Mute B")):
                    ids.append((await post(client, "/api/auth/guest", {"display_name": name})).json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Mute room", "seat_limit": 2})).json()
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                for client in (first, second):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                chat_base = f"{base}/chat"
                literal = '<img src=x onerror="alert(1)">'
                original = message("common", literal)
                sent = await post(first, f"{chat_base}/messages", original)
                assert sent.status_code == 200
                assert (await post(first, f"{chat_base}/messages", original)).json()["repeated"] is True
                assert (await post(first, f"{chat_base}/messages", message("common", "Hai"))).status_code == 200
                limited = await post(first, f"{chat_base}/messages", message("common", "Ba"))
                assert limited.status_code == 429 and limited.headers["Retry-After"] == "10"
                monkeypatch.setattr(chat, "CHAT_BURST_LIMIT", 100)
                monkeypatch.setattr(chat, "CHAT_MINUTE_LIMIT", 2)
                minute_limited = await post(first, f"{chat_base}/messages", message("common", "Ba"))
                assert minute_limited.status_code == 429 and minute_limited.headers["Retry-After"] == "60"
                assert (await second.get(f"{chat_base}/messages?channel=common")).json()["messages"][0]["text"] == literal
                assert (await post(first, f"{chat_base}/messages", message("common", "x" * 2001))).status_code == 422

                monkeypatch.setattr(chat, "CHAT_MINUTE_LIMIT", 100)
                target = f"{chat_base}/players/{ids[1]}/mute"
                payload = {"client_request_id": str(uuid4()), "muted": True, "reason": "Tạm nghỉ"}
                assert (await first.patch(target, json=payload, headers={"Origin": ORIGIN})).status_code == 403
                assert (await host.patch(target, json={**payload, "reason": "  "}, headers={"Origin": ORIGIN})).status_code == 422
                muted = await host.patch(target, json=payload, headers={"Origin": ORIGIN})
                assert muted.status_code == 200 and not muted.json()["repeated"]
                assert (await host.patch(target, json=payload, headers={"Origin": ORIGIN})).json()["repeated"]
                assert (await host.patch(target, json={**payload, "reason": "Khác"}, headers={"Origin": ORIGIN})).status_code == 409
                context = (await first.get(f"{chat_base}/context")).json()
                assert context["is_muted"] and context["mute_reason"] == "Tạm nghỉ"
                assert not context["can_send_common"]
                assert (await post(first, f"{chat_base}/messages", message("common", "Bị chặn"))).status_code == 403
                assert (await first.get(f"{chat_base}/messages?channel=common")).status_code == 200
                host_context = (await host.get(f"{chat_base}/context")).json()
                assert host_context["muted_players"] == [{"user_id": ids[1], "reason": "Tạm nghỉ"}]
                assert (await second.get(f"{chat_base}/context")).json()["muted_players"] == []
                async with session_factory()() as db:
                    event = await db.get(GameEvent, UUID(muted.json()["event_id"]))
                    assert event.visibility == "host" and event.payload["request"]["user_id"] == ids[1]
                    assert (await db.scalar(select(Outbox.id).where(Outbox.event_id == event.id))) is not None
                opened = await host.patch(target, json={"client_request_id": str(uuid4()),
                    "muted": False, "reason": "Đã trao đổi"}, headers={"Origin": ORIGIN})
                assert opened.status_code == 200
                assert (await first.get(f"{chat_base}/context")).json()["is_muted"] is False
                assert (await post(first, f"{chat_base}/messages", message("common", "Đã quay lại"))).status_code == 200
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())


def test_host_chat_revisions_current_view_and_postgame_history() -> None:
    async def run() -> None:
        ids = []
        room_id = None
        async with AsyncExitStack() as stack:
            transport = httpx.ASGITransport(app=app)
            host, wolf, villager, outsider = [await stack.enter_async_context(httpx.AsyncClient(
                transport=transport, base_url="http://test")) for _ in range(4)]
            try:
                for client, name in ((host, "Host sửa chat"), (wolf, "Sói chat"),
                                     (villager, "Dân chat"), (outsider, "Ngoài chat")):
                    ids.append((await post(client, "/api/auth/guest", {"display_name": name})).json()["id"])
                room = (await post(host, "/api/rooms", {"name": "Sửa chat", "seat_limit": 2})).json()
                room_id = room["id"]
                base = f"/api/rooms/{room_id}"
                chat_base = f"{base}/chat"
                for client in (wolf, villager):
                    assert (await post(client, "/api/rooms/join", {
                        "code": room["code"], "accept_postgame_transcript": True})).status_code == 200
                for name, team in (("Sói thử", "Sói"), ("Dân thử", None)):
                    assert (await post(host, f"{base}/roles", {
                        "name": name, "description": name, "faction_label": name,
                        "seer_result": name, "team_chat_label": team,
                        "role_kind": "passive", "quantity": 1,
                        "host_contact": {"reply_allowed": True},
                    })).status_code == 201
                assert (await host.patch(f"{base}/settings", json={
                    "name": "Sửa chat", "seat_limit": 2, "registration_open": False},
                    headers={"Origin": ORIGIN})).status_code == 200
                assert (await post(host, f"{base}/roles/distribute-random")).status_code == 200
                assignments = (await host.get(f"{base}/roles/assignments")).json()
                wolf_id = next(item["user_id"] for item in assignments if item["role"]["team_chat_label"])
                wolf_client = wolf if wolf_id == ids[1] else villager
                other_client = villager if wolf_client is wolf else wolf
                other_id = ids[2] if wolf_client is wolf else ids[1]
                assert (await post(host, f"{base}/game/start")).status_code == 200
                common_event = (await post(host, f"{chat_base}/messages", message("common", "Tin gốc"))).json()["event_id"]
                player_event = (await post(wolf_client, f"{chat_base}/messages", message("common", "Tin người chơi"))).json()["event_id"]
                private_event = (await post(host, f"{chat_base}/messages", message(
                    "host", "Tin riêng gốc", participant_user_id=wolf_id))).json()["event_id"]
                assert (await post(host, f"{base}/game/advance", {
                    "expected_phase_type": "day", "expected_round_number": 0})).status_code == 200
                team_event = (await post(host, f"{chat_base}/messages", message(
                    "team", "Tin đội gốc", team_label="Sói"))).json()["event_id"]
                assert (await post(host, f"{base}/game/players/{other_id}/actions", {
                    "client_request_id": str(uuid4()), "action": "change_team",
                    "text": "Sói", "reason": "Kiểm tra người vào đội sau khi tin được gửi",
                })).status_code == 200
                content = f"{base}/game/content"
                assert (await post(host, f"{content}/{player_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "Không được"})).status_code == 409
                assert (await post(wolf_client, f"{content}/{common_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "Không được"})).status_code == 403
                cursor = (await wolf_client.get(f"{chat_base}/messages?channel=common")).json()["room_cursor"]
                edit_body = {"client_request_id": str(uuid4()), "action": "edit", "text": "Tin mới"}
                changed = await post(host, f"{content}/{common_event}/revisions", edit_body)
                assert changed.status_code == 200, changed.text
                assert (await post(host, f"{content}/{common_event}/revisions", edit_body)).json()["repeated"]
                assert (await post(host, f"{content}/{common_event}/revisions", {
                    **edit_body, "text": "Khác"})).status_code == 409
                page = (await wolf_client.get(f"{chat_base}/messages?channel=common")).json()
                assert next(item for item in page["messages"] if item["event_id"] == common_event)["text"] == "Tin mới"
                updated = (await wolf_client.get(f"{chat_base}/messages?channel=common&after_updated_sequence={cursor}")).json()
                assert [item["text"] for item in updated["messages"]] == ["Tin mới"]
                assert updated["messages"][0]["edited_at"]
                assert (await post(host, f"{content}/{team_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "Tin đội mới"})).status_code == 200
                assert (await wolf_client.get(f"{chat_base}/messages?channel=team")).json()["messages"][0]["text"] == "Tin đội mới"
                assert (await other_client.get(f"{chat_base}/messages?channel=team")).json()["messages"] == []
                assert (await other_client.get(f"{chat_base}/messages?channel=team&after_updated_sequence=0")).json()["messages"] == []
                assert (await post(host, f"{content}/{private_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "Tin riêng mới"})).status_code == 200
                assert (await wolf_client.get(f"{chat_base}/messages?channel=host")).json()["messages"][0]["text"] == "Tin riêng mới"
                assert (await other_client.get(f"{chat_base}/messages?channel=host")).json()["messages"] == []
                notice_event = (await post(host, f"{base}/game/events/announcements", {
                    "client_request_id": str(uuid4()), "text": "Thông báo gốc"})).json()["event_id"]
                assert (await post(host, f"{content}/{notice_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "Thông báo mới"})).status_code == 200
                assert (await post(host, f"{content}/{notice_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "delete"})).status_code == 200
                assert (await wolf_client.get(f"{content}/history")).status_code == 409
                deletion = await post(host, f"{content}/{common_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "delete"})
                assert deletion.status_code == 200
                assert (await post(host, f"{content}/{common_event}/revisions", {
                    "client_request_id": str(uuid4()), "action": "edit", "text": "Hồi lại"})).status_code == 409
                current = (await wolf_client.get(f"{chat_base}/messages?channel=common")).json()["messages"]
                assert common_event not in [item["event_id"] for item in current]
                tombstone = (await wolf_client.get(f"{chat_base}/messages?channel=common&after_updated_sequence={cursor}")).json()["messages"]
                assert len(tombstone) == 1 and tombstone[0]["deleted"] and tombstone[0]["text"] == ""
                async with session_factory()() as db:
                    update = await db.get(GameEvent, UUID(deletion.json()["public_event_id"]))
                    team_updates = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind == "chat.revised",
                        GameEvent.payload["source_event_id"].astext == team_event))).all()
                    private_updates = (await db.scalars(select(GameEvent).where(
                        GameEvent.room_id == UUID(room_id), GameEvent.kind == "chat.revised",
                        GameEvent.payload["source_event_id"].astext == private_event))).all()
                    assert update.visibility == "room"
                    assert may_receive(team_updates[0], UUID(wolf_id), UUID(ids[0]), None, None)
                    assert not may_receive(team_updates[0], UUID(other_id), UUID(ids[0]), None, None)
                    assert may_receive(private_updates[0], UUID(wolf_id), UUID(ids[0]), None, None)
                    assert not may_receive(private_updates[0], UUID(other_id), UUID(ids[0]), None, None)
                    assert (await db.scalar(select(Outbox.id).where(Outbox.event_id == update.id))) is not None
                assert (await post(host, f"{base}/game/finish", {
                    "result_label": "Kết thúc", "result_reason": "Kiểm tra lịch sử"})).status_code == 200
                history = (await other_client.get(f"{chat_base}/messages?channel=common")).json()["messages"]
                deleted = next(item for item in history if item["event_id"] == common_event)
                assert deleted["deleted"] and [item["action"] for item in deleted["history"]] == ["send", "edit", "delete"]
                assert [item["text"] for item in deleted["history"]] == ["Tin gốc", "Tin mới", None]
                assert len((await other_client.get(f"{chat_base}/messages?channel=team")).json()["messages"]) == 1
                assert len((await other_client.get(f"{chat_base}/messages?channel=host")).json()["messages"]) == 1
                assert (await outsider.get(f"{chat_base}/messages?channel=host")).status_code == 403
                notice_history = (await other_client.get(f"{content}/history?limit=1")).json()
                assert notice_history["items"][0]["action"] == "delete"
                assert notice_history["items"][0]["previous_text"] == "Thông báo mới"
                assert notice_history["next_before_sequence"]
                older_history = (await other_client.get(
                    f"{content}/history?limit=1&before_sequence={notice_history['next_before_sequence']}")).json()
                assert older_history["items"][0]["action"] == "edit"
                assert older_history["items"][0]["previous_text"] == "Thông báo gốc"
                assert older_history["items"][0]["new_text"] == "Thông báo mới"
                assert (await outsider.get(f"{content}/history")).status_code == 403
            finally:
                if room_id:
                    async with session_factory()() as db:
                        await db.delete(await db.get(Room, UUID(room_id)))
                        await db.commit()
                        for user_id in ids:
                            await db.delete(await db.get(User, UUID(user_id)))
                        await db.commit()

    asyncio.run(run())
