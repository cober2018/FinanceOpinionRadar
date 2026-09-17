"""source_account 直播值守字段迁移与 repository 集成测试（Plan #4 Task 3 Step 1/2）。"""

from app.db.models import Creator, SourceAccount
from app.repositories.source_accounts import SourceAccountRepository
from sqlalchemy import select


def _make_account(session, *, platform="douyin", external_id="MS4wLjABlive1", **over):
    creator = Creator(display_name=f"creator_{external_id}", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform=platform,
        external_id=external_id,
        url=f"https://www.douyin.com/user/{external_id}",
        discovery_mode="manual",
        poll_interval_sec=3600,
    )
    for k, v in over.items():
        setattr(account, k, v)
    session.add(account)
    session.flush()
    return account


def test_live_fields_defaults(db_session):
    account = _make_account(db_session)
    db_session.refresh(account)
    assert account.live_monitor_enabled is False
    assert account.monitor_interval_sec == 300
    assert account.expected_schedule is None


def test_live_fields_roundtrip(db_session):
    schedule = {"days": ["sat", "sun"], "start": "20:00", "end": "23:00"}
    account = _make_account(
        db_session,
        live_monitor_enabled=True,
        monitor_interval_sec=600,
        expected_schedule=schedule,
    )
    db_session.commit()
    db_session.expire_all()
    fresh = db_session.get(SourceAccount, account.id)
    assert fresh.live_monitor_enabled is True
    assert fresh.monitor_interval_sec == 600
    assert fresh.expected_schedule == schedule


def test_list_live_monitored_filters_enabled_and_flag(db_session):
    on = _make_account(db_session, external_id="live_on", live_monitor_enabled=True)
    _make_account(db_session, external_id="flag_off", live_monitor_enabled=False)
    _make_account(
        db_session, external_id="disabled", live_monitor_enabled=True, enabled=False
    )
    db_session.commit()

    got = SourceAccountRepository(db_session).list_live_monitored()
    assert [a.id for a in got] == [on.id]


def test_list_live_monitored_returns_only_requested_columns_order_stable(db_session):
    a = _make_account(db_session, external_id="live_a", live_monitor_enabled=True)
    b = _make_account(db_session, external_id="live_b", live_monitor_enabled=True)
    db_session.commit()
    got = SourceAccountRepository(db_session).list_live_monitored()
    assert {x.id for x in got} == {a.id, b.id}
    # 复用同一 session 语义：返回 ORM 对象（bridge 需要读 url / monitor_interval_sec）
    stmt = select(SourceAccount).where(SourceAccount.id == a.id)
    assert db_session.scalars(stmt).one().live_monitor_enabled is True
