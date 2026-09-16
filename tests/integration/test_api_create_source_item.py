"""POST /source-items 端到端（真库 + StubAdapter）：创建与幂等。"""

import pytest
from app.db.models import SourceItem
from app.main import app
from app.services.media.factory import get_media_adapter
from fastapi.testclient import TestClient

from tests.fixtures.media.payloads import URL
from tests.integration.test_discovery_service import StubAdapter, _resolved


@pytest.fixture
def client(db_session):
    # get_db 也必须 override：否则 TestClient 会把测试数据写进开发库
    from app.db.session import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_media_adapter] = lambda: StubAdapter(_resolved())
    return TestClient(app)


def teardown_module():
    app.dependency_overrides.pop(get_media_adapter, None)
    from app.db.session import get_db

    app.dependency_overrides.pop(get_db, None)


def test_create_source_item_201_and_idempotent(client, db_session):
    resp = client.post("/api/v1/source-items", json={"url": URL})
    assert resp.status_code == 201
    body = resp.json()
    assert body["external_item_id"] == "abc123"
    assert body["status"] == "discovered"
    assert body["id"] is not None

    # 幂等：重复提交同 URL 返回同一行
    resp2 = client.post("/api/v1/source-items", json={"url": URL})
    assert resp2.status_code == 201
    assert resp2.json()["id"] == body["id"]
    assert db_session.query(SourceItem).filter(
        SourceItem.external_item_id == "abc123"
    ).count() == 1
