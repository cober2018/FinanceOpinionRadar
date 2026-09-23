"""开放 API 鉴权与调用审计（Plan #7）：401/200/吊销 + api_call_log 记账。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.open import deps as open_deps
from app.db.models import ApiCallLog, ApiKey
from app.db.session import get_db


@pytest.fixture
def client(db_session, migrated_db, monkeypatch):
    # 审计 middleware 用独立会话工厂——替换成测试库，避免写开发库
    engine = create_engine(migrated_db)
    monkeypatch.setattr(open_deps, "audit_session", sessionmaker(bind=engine))
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


RAW_KEY = "rk_" + "a1b2c3d4" * 4


def _mk_key(db_session, *, status="active") -> ApiKey:
    from app.api.open.deps import hash_key

    row = ApiKey(name="测试 Key", key_hash=hash_key(RAW_KEY), prefix=RAW_KEY[:8], status=status)
    db_session.add(row)
    db_session.commit()
    return row


def test_missing_or_invalid_key_401(client, db_session):
    _mk_key(db_session)
    r = client.get("/open/v1/creators")
    assert r.status_code == 401
    r = client.get("/open/v1/creators", headers={"X-API-Key": "rk_wrong"})
    assert r.status_code == 401


def test_valid_key_200_and_audited(client, db_session):
    _mk_key(db_session)
    r = client.get("/open/v1/creators", headers={"X-API-Key": RAW_KEY})
    assert r.status_code == 200 and r.json()["items"] == []
    db_session.expire_all()
    logs = db_session.query(ApiCallLog).order_by(ApiCallLog.id).all()
    assert len(logs) == 1
    assert logs[0].status_code == 200 and logs[0].path == "/open/v1/creators"
    assert logs[0].api_key_id is not None  # 归因到 key


def test_revoked_key_401_and_failed_auth_audited(client, db_session):
    key = _mk_key(db_session)
    key.status = "revoked"
    db_session.commit()
    r = client.get("/open/v1/creators", headers={"X-API-Key": RAW_KEY})
    assert r.status_code == 401
    db_session.expire_all()
    log = db_session.query(ApiCallLog).one()
    assert log.api_key_id is None  # 鉴权失败也记账，key 归因为空


def test_non_open_path_not_audited(client, db_session):
    _mk_key(db_session)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.query(ApiCallLog).count() == 0  # 审计只拦 /open/
