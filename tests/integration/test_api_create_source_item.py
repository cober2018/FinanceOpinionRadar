"""POST /source-items 端到端（真库 + StubAdapter）：创建与幂等。"""

import pytest
from app.api.v1.source_items import _create_adapter
from app.db.models import SourceItem
from app.main import app
from fastapi.testclient import TestClient

from tests.fixtures.media.payloads import URL
from tests.integration.test_discovery_service import StubAdapter, _resolved


@pytest.fixture
def client(db_session):
    # get_db 也必须 override：否则 TestClient 会把测试数据写进开发库
    from app.api.v1.source_items import CreateSourceItemRequest
    from app.db.session import get_db

    def fake_create(body: CreateSourceItemRequest) -> StubAdapter:
        # override 按替身签名解析参数，body 必须带类型标注（否则被当 query 参数 422）
        return StubAdapter(_resolved())

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[_create_adapter] = fake_create
    return TestClient(app)


def teardown_module():
    app.dependency_overrides.pop(_create_adapter, None)
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


def test_manual_registration_normalizes_channel_url(client, db_session):
    # ENG-2A：注册侧 normalize 接线生效——裸频道地址入库前补 /videos（注记③）
    from app.db.models import SourceAccount
    from app.services import discovery

    bare = "https://www.youtube.com/@macro-diary"
    adapter = StubAdapter(_resolved(channel_url=bare))
    item = discovery.create_item_from_url(URL, db_session, adapter)
    assert item.id is not None
    account = (
        db_session.query(SourceAccount)
        .filter(SourceAccount.external_id == "ch_42")
        .one()
    )
    assert account.url == f"{bare}/videos"
