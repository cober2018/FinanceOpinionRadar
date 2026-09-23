"""删除端点墓碑幂等（750 实录）：条目曾被删又被 live 重建，同键墓碑已存在时不得 500。"""

import pytest
from fastapi.testclient import TestClient

from app.db.models import Creator, DeletedItemRef, SourceAccount, SourceItem
from app.db.session import get_db


@pytest.fixture
def client(db_session):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _mk_item(db_session, external: str) -> SourceItem:
    creator = Creator(display_name="主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec1", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=external,
        item_type="live",
        status="ready",
    )
    db_session.add(item)
    db_session.commit()
    return item


def test_delete_with_existing_tombstone_succeeds(db_session, client):
    item = _mk_item(db_session, "live:sec1:2026-09-17")
    db_session.add(
        DeletedItemRef(source_account_id=item.source_account_id, external_item_id=item.external_item_id)
    )
    db_session.commit()

    r = client.delete(f"/api/v1/source-items/{item.id}")
    assert r.status_code == 200  # 不再撞 uq_deleted_item_ref
    assert db_session.query(SourceItem).count() == 0
    assert db_session.query(DeletedItemRef).count() == 1  # 墓碑不重复插入


def test_batch_delete_with_existing_tombstone(db_session, client):
    a = _mk_item(db_session, "live:sec1:2026-09-18")
    db_session.add(
        DeletedItemRef(source_account_id=a.source_account_id, external_item_id=a.external_item_id)
    )
    db_session.commit()

    r = client.post("/api/v1/source-items/batch-delete", json={"ids": [a.id]})
    assert r.status_code == 200 and r.json()["deleted"] == [a.id]
    assert db_session.query(SourceItem).count() == 0
