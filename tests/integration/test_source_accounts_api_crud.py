"""账号管理 API CRUD 集成测试（Plan #4 Task 3 Step 3/4）：真库 + TestClient。"""

import pytest
from app.db.models import Creator, SourceAccount
from app.db.session import get_db
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


DOUYIN_URL = "https://www.douyin.com/user/MS4wLjABdyapi01"
SEC_UID = "MS4wLjABdyapi01"


def test_post_creates_douyin_account_201(db_session, client):
    resp = client.post(
        "/api/v1/source-accounts",
        json={
            "platform": "douyin",
            "url": DOUYIN_URL,
            "display_name": "全能的野人",
            "discovery_mode": "auto_poll",
            "poll_interval_sec": 1800,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["external_id"] == SEC_UID
    assert body["discovery_mode"] == "auto_poll"
    assert body["poll_interval_sec"] == 1800
    assert body["enabled"] is True
    assert body["live_monitor_enabled"] is False

    # creator get-or-create（复用 discovery 逻辑抽函数）
    account = db_session.get(SourceAccount, body["id"])
    assert account is not None
    creator = db_session.get(Creator, account.creator_id)
    assert creator is not None and creator.display_name == "全能的野人"


def test_post_is_idempotent_on_same_sec_uid(db_session, client):
    payload = {"platform": "douyin", "url": DOUYIN_URL}
    first = client.post("/api/v1/source-accounts", json=payload)
    second = client.post("/api/v1/source-accounts", json=payload)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_get_list_filters_platform_and_enabled(db_session, client):
    client.post("/api/v1/source-accounts", json={"platform": "douyin", "url": DOUYIN_URL})
    resp = client.get("/api/v1/source-accounts", params={"platform": "douyin"})
    assert resp.status_code == 200
    assert any(a["external_id"] == SEC_UID for a in resp.json())

    empty = client.get("/api/v1/source-accounts", params={"platform": "bilibili"})
    assert all(a["platform"] == "bilibili" for a in empty.json())


def test_patch_updates_live_fields_partially(db_session, client):
    created = client.post(
        "/api/v1/source-accounts", json={"platform": "douyin", "url": DOUYIN_URL}
    ).json()
    resp = client.patch(
        f"/api/v1/source-accounts/{created['id']}",
        json={
            "live_monitor_enabled": True,
            "monitor_interval_sec": 600,
            "expected_schedule": {"days": ["sat"], "start": "20:00", "end": "23:00"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["live_monitor_enabled"] is True
    assert body["monitor_interval_sec"] == 600
    assert body["expected_schedule"] == {"days": ["sat"], "start": "20:00", "end": "23:00"}
    # 局部更新：未触碰字段保持原值
    assert body["discovery_mode"] == "manual"


def test_patch_unknown_id_404(client):
    resp = client.patch(
        "/api/v1/source-accounts/999999", json={"enabled": False}
    )
    assert resp.status_code == 404


def test_patch_rejects_bad_discovery_mode_422(db_session, client):
    created = client.post(
        "/api/v1/source-accounts", json={"platform": "douyin", "url": DOUYIN_URL}
    ).json()
    resp = client.patch(
        f"/api/v1/source-accounts/{created['id']}", json={"discovery_mode": "turbo"}
    )
    assert resp.status_code == 422
