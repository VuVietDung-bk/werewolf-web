"""Browser trust boundaries and access-log redaction."""

import asyncio
import logging

import httpx

from app import main
from app.auth import origin_allowed


def test_origin_allowlist_rejects_missing_null_and_lookalike(monkeypatch) -> None:
    monkeypatch.setenv("APP_ORIGINS", "https://game.example")
    assert origin_allowed("https://game.example")
    for source in (None, "null", "https://game.example.attacker.test",
                   "http://game.example", "https://game.example:443"):
        assert not origin_allowed(source)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test") as client:
            for source in (None, "null", "https://game.example.attacker.test"):
                headers = {} if source is None else {"Origin": source}
                response = await client.post("/api/auth/guest", json={"display_name": "X"},
                                             headers=headers)
                assert response.status_code == 403
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Cache-Control"] == "no-store"

    asyncio.run(run())


def test_access_log_omits_query_and_headers(monkeypatch) -> None:
    entries: list[tuple] = []
    monkeypatch.setattr(main.access_logger, "info", lambda *args: entries.append(args))
    assert logging.getLogger("uvicorn.access").disabled

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test") as client:
            response = await client.get("/api/health/live?q=private-chat-secret",
                                        headers={"Cookie": "ma_soi_session=secret-cookie"})
            assert response.status_code == 200
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            await client.get("/api/unknown/private-chat-secret?q=private-chat-secret")

    asyncio.run(run())
    assert entries == [("HTTP %s %s %d", "GET", "/api/health/live", 200),
                       ("HTTP %s %s %d", "GET", "unmatched", 404)]
    assert "private-chat-secret" not in repr(entries)
    assert "secret-cookie" not in repr(entries)


def test_websocket_server_log_redacts_raw_url() -> None:
    assert any(isinstance(item, main.WebSocketPathFilter)
               for item in logging.getLogger("uvicorn.error").filters)
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                               '%s - "WebSocket %s" [accepted]',
                               ("test:1234", "/api/ws/session?token=private-chat-secret"), None)
    assert main.WebSocketPathFilter().filter(record)
    assert "private-chat-secret" not in record.getMessage()
    assert "[redacted]" in record.getMessage()
