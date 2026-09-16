from app.db.models import Creator, SourceAccount
from app.repositories.source_items import SourceItemRepository
from sqlalchemy.orm import Session


def _account(session: Session, external_id: str = "ch1") -> SourceAccount:
    creator = Creator(display_name="测试作者", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="youtube", external_id=external_id, discovery_mode="manual"
    )
    session.add(account)
    session.flush()
    return account


def test_upsert_creates_then_updates(db_session: Session) -> None:
    repo = SourceItemRepository(db_session)
    account = _account(db_session)

    item, created = repo.upsert_by_external(
        source_account_id=account.id,
        external_item_id="v1",
        title="初版标题",
        canonical_url="https://www.youtube.com/watch?v=v1",
    )
    assert created is True
    assert item.id is not None

    # 二次 upsert：同 (account, external) 不产生新行，字段刷新
    updated, created2 = repo.upsert_by_external(
        source_account_id=account.id,
        external_item_id="v1",
        title="新标题",
        canonical_url="https://www.youtube.com/watch?v=v1",
        duration_ms=123000,
    )
    db_session.flush()
    assert created2 is False
    assert updated.id == item.id
    assert updated.title == "新标题"
    assert updated.duration_ms == 123000
    assert repo.count() == 1


def test_same_external_id_isolated_per_account(db_session: Session) -> None:
    # 同一 external_item_id 挂在不同账号 → 两行互不串号（唯一键按账号隔离）
    repo = SourceItemRepository(db_session)
    a1 = _account(db_session, external_id="ch1")
    a2 = _account(db_session, external_id="ch2")

    i1, c1 = repo.upsert_by_external(
        source_account_id=a1.id, external_item_id="vX", title="账号一"
    )
    i2, c2 = repo.upsert_by_external(
        source_account_id=a2.id, external_item_id="vX", title="账号二"
    )
    assert c1 and c2
    assert i1.id != i2.id
    assert repo.count() == 2
