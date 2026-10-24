"""Metrics stay private, bounded, and useful when services fail."""

import asyncio
import time

import httpx

from app import main, outbox_worker
from app.observability import HttpMetrics, http_metrics


class Result:
    def one(self):
        return (2, 31.5, 1, 123456, 3)


class Database:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, _):
        return Result()


class Redis:
    def __init__(self):
        self.closed = False

    async def ping(self):
        return True

    async def get(self, _):
        return str(time.time() - 3)

    async def aclose(self):
        self.closed = True


def test_private_metrics_and_bounded_routes(monkeypatch) -> None:
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(main, "session_factory", lambda: lambda: Database())
    redis = Redis()
    monkeypatch.setattr(main, "redis_client", lambda _: redis)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test") as client:
            assert (await client.get("/internal/metrics", headers={
                "Authorization": "Bearer ops-test-secret"})).status_code == 404
            monkeypatch.setenv("METRICS_TOKEN", "ops-test-secret")
            assert (await client.get("/internal/metrics")).status_code == 404
            assert (await client.get("/internal/metrics", headers={
                "Authorization": "Bearer wrong"})).status_code == 404
            await client.get("/api/health/live")
            secret_id = "00000000-0000-4000-8000-000000000999"
            http_metrics.record("GET", "/api/rooms/{room_id}", 401, 0.01)
            response = await client.get("/internal/metrics", headers={
                "Authorization": "Bearer ops-test-secret"})
            assert response.status_code == 200
            body = response.text
            assert 'route="/api/health/live"' in body
            assert 'route="/api/rooms/{room_id}"' in body
            assert secret_id not in body and "ops-test-secret" not in body
            assert "ma_soi_outbox_pending 2" in body
            assert "ma_soi_database_size_bytes 123456" in body
            assert "ma_soi_postgres_up 1" in body
            assert "ma_soi_redis_up 1" in body
            assert "ma_soi_outbox_worker_heartbeat_age_seconds" in body
            assert redis.closed

            class BrokenDatabase(Database):
                async def __aenter__(self):
                    raise OSError("database unavailable")

            monkeypatch.setattr(main, "session_factory", lambda: lambda: BrokenDatabase())
            degraded = await client.get("/internal/metrics", headers={
                "Authorization": "Bearer ops-test-secret"})
            assert degraded.status_code == 200
            assert "ma_soi_postgres_up 0" in degraded.text

    asyncio.run(run())


def test_histogram_is_cumulative_and_worker_heartbeat(monkeypatch) -> None:
    stats = HttpMetrics()
    stats.record("POST", "/api/rooms", 500, 0.2)
    stats.record("POST", "/api/rooms", 201, 0.8)
    stats.record("CUSTOM-UNBOUNDED", "unmatched", 404, 0.001)
    body = stats.render()
    assert 'method="OTHER"' in body and "CUSTOM-UNBOUNDED" not in body
    assert 'status_class="5xx"} 1' in body
    assert 'le="0.25"} 1' in body
    assert 'le="1"} 2' in body
    assert 'le="+Inf"} 2' in body

    stop = asyncio.Event()
    sent = []

    class WorkerRedis:
        async def set(self, key, value, ex):
            sent.append((key, float(value), ex))
            stop.set()

        async def aclose(self):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    async def empty_batch(*_):
        return 0, False

    monkeypatch.setattr(outbox_worker, "redis_client", lambda _: WorkerRedis())
    monkeypatch.setattr(outbox_worker, "session_factory", lambda: lambda: Session())
    monkeypatch.setattr(outbox_worker, "publish_batch", empty_batch)
    asyncio.run(outbox_worker.run_worker("redis://unused", stop))
    assert len(sent) == 1 and sent[0][0] == outbox_worker.HEARTBEAT_KEY
    assert abs(time.time() - sent[0][1]) < 5 and sent[0][2] == 20
