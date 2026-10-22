import asyncio

import httpx
import psycopg

from wallet.main import app


def get(path):
    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_live_has_request_id():
    response = get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_ready_reports_database_failure(monkeypatch):
    def unavailable(**kwargs):
        raise psycopg.OperationalError("offline")

    monkeypatch.setattr("wallet.main.connect", unavailable)
    response = get("/health/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
