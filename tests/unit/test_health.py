from app.main import app
from fastapi.testclient import TestClient


def test_health_returns_ok() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
