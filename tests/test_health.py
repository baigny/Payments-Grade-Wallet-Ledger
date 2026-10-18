import psycopg
from fastapi.testclient import TestClient

from wallet.main import app

client = TestClient(app)


def test_live_has_request_id():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_ready_reports_database_failure(monkeypatch):
    def unavailable(**kwargs):
        raise psycopg.OperationalError("offline")

    monkeypatch.setattr("wallet.main.connect", unavailable)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
