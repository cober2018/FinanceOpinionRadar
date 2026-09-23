"""开放层管理端点（Plan #7）：API Key 创建/吊销、推送渠道 CRUD + test 消息。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.open import deps as open_deps
from app.db.models import ApiKey, PushChannel
from app.db.session import get_db


@pytest.fixture
def client(db_session, migrated_db, monkeypatch):
    engine = create_engine(migrated_db)
    monkeypatch.setattr(open_deps, "audit_session", sessionmaker(bind=engine))
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


def test_api_key_lifecycle(client, db_session):
    r = client.post("/api/v1/open-admin/api-keys", json={"name": "兄弟产品 A"})
    assert r.status_code == 201
    raw = r.json()["api_key"]
    assert raw.startswith("rk_") and len(raw) == 3 + 32  # 明文仅此一次回显

    # 列表不显明文，只显 prefix
    listing = client.get("/api/v1/open-admin/api-keys").json()["items"]
    assert listing[0]["prefix"] == raw[:8] and "api_key" not in listing[0]

    # 明文可鉴权访问开放端点
    assert client.get("/open/v1/entities", headers={"X-API-Key": raw}).status_code == 200

    # 吊销后 401
    kid = listing[0]["id"]
    assert client.delete(f"/api/v1/open-admin/api-keys/{kid}").status_code == 200
    assert client.get("/open/v1/entities", headers={"X-API-Key": raw}).status_code == 401
    db_session.expire_all()
    assert db_session.query(ApiKey).one().status == "revoked"


def test_push_channel_crud_and_enabled_toggle(client, db_session):
    r = client.post(
        "/api/v1/open-admin/push-channels",
        json={"name": "运营群", "channel_type": "feishu", "url": "https://open.feishu.cn/hook/x"},
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["has_secret"] is False

    r = client.patch(f"/api/v1/open-admin/push-channels/{cid}", json={"enabled": False})
    assert r.json()["enabled"] is False

    # secret 只写不读（has_secret 布尔化）
    client.patch(
        f"/api/v1/open-admin/push-channels/{cid}", json={"secret": "topsecret"}
    )
    row = client.get("/api/v1/open-admin/push-channels").json()["items"][0]
    assert row["has_secret"] is True and "secret" not in row

    assert client.delete(f"/api/v1/open-admin/push-channels/{cid}").status_code == 200
    assert db_session.query(PushChannel).count() == 0


def test_push_channel_test_message_error_bubbles_502(client, db_session):
    r = client.post(
        "/api/v1/open-admin/push-channels",
        json={
            "name": "坏渠道",
            "channel_type": "generic_webhook",
            "url": "http://127.0.0.1:1/nope",  # 不可达端口 → 连接失败
        },
    )
    cid = r.json()["id"]
    resp = client.post(f"/api/v1/open-admin/push-channels/{cid}/test")
    assert resp.status_code == 502
    assert "推送测试失败" in resp.json()["detail"]
