import threading

from app.db.models import Creator, SourceAccount, SourceItem, Topic, Viewpoint
from app.repositories import (
    CreatorRepository,
    SourceAccountRepository,
    ViewpointRepository,
)
from sqlalchemy.orm import sessionmaker


def test_creator_repo_returns_domain_type(db_session) -> None:
    repo = CreatorRepository(db_session)
    creator = repo.create(display_name="张三")
    db_session.commit()
    fetched = repo.get(creator.id)
    assert isinstance(fetched, Creator)
    assert fetched.display_name == "张三"
    assert repo.get(999999) is None


def test_source_account_repo_upsert_idempotent(db_session) -> None:
    creators = CreatorRepository(db_session)
    accounts = SourceAccountRepository(db_session)
    creator = creators.create(display_name="李四")

    a1 = accounts.upsert_by_external(
        creator_id=creator.id, platform="bilibili", external_id="UID_1", handle="lisi"
    )
    db_session.commit()
    a2 = accounts.upsert_by_external(
        creator_id=creator.id, platform="bilibili", external_id="UID_1", handle="lisi-v2"
    )
    db_session.commit()

    assert a1.id == a2.id
    assert a2.handle == "lisi-v2"  # 更新而非新建
    assert accounts.count() == 1  # 幂等（执行计划 RAD-023 依赖）


def test_source_account_upsert_concurrent_no_integrity_error(db_session) -> None:
    """D39：4 线程并发对同一 (platform, external_id) upsert，不抛 IntegrityError 且仅 1 行。

    会话非线程安全，各线程用独立 session（复用同一 engine，D58）。
    """
    creators = CreatorRepository(db_session)
    creator = creators.create(display_name="并发王")
    db_session.commit()
    engine = db_session.get_bind()

    errors: list[Exception] = []

    def _worker(i: int) -> None:
        factory = sessionmaker(bind=engine)
        session = factory()
        try:
            SourceAccountRepository(session).upsert_by_external(
                creator_id=creator.id,
                platform="youtube",
                external_id="UC_CONCURRENT",
                handle=f"@w{i}",
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 收集任一异常供断言
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert SourceAccountRepository(db_session).count() == 1


def test_viewpoint_repo_orders_by_asof(db_session) -> None:
    """D40：时间线按 as_of_date DESC NULLS LAST，再按 created_at DESC。"""
    creators = CreatorRepository(db_session)
    creator = creators.create(display_name="时序员")
    db_session.flush()
    account = SourceAccount(creator_id=creator.id, platform="youtube", external_id="UC_T")
    db_session.add(account)
    db_session.flush()
    topic = Topic(canonical_name="黄金")
    db_session.add(topic)
    db_session.flush()

    from datetime import date, datetime, timezone

    old_item = SourceItem(
        source_account_id=account.id,
        external_item_id="v-old",
        published_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    new_item = SourceItem(
        source_account_id=account.id,
        external_item_id="v-new",
        published_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
    )
    no_date_item = SourceItem(source_account_id=account.id, external_item_id="v-nodate")
    db_session.add_all([old_item, new_item, no_date_item])
    db_session.flush()

    def _vp(item: SourceItem, as_of: date | None, claim: str) -> Viewpoint:
        return Viewpoint(
            creator_id=creator.id,
            source_item_id=item.id,
            topic_id=topic.id,
            claim=claim,
            stance="bullish",
            horizon="1-3M",
            as_of_date=as_of,
        )

    # 无 as_of 的先插入（created_at 最早），验证 NULLS LAST 且 as_of 主导排序
    db_session.add(_vp(no_date_item, None, "no-date"))
    db_session.flush()
    db_session.add(_vp(old_item, date(2026, 1, 5), "old"))
    db_session.flush()
    db_session.add(_vp(new_item, date(2026, 3, 10), "new"))
    db_session.commit()

    repo = ViewpointRepository(db_session)
    timeline = repo.list_by_creator_topic(creator.id, topic.id)
    assert [v.claim for v in timeline] == ["new", "old", "no-date"]
