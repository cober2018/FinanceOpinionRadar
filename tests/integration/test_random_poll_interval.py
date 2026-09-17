"""source_account 人类化随机轮询区间：字段回环 + 成功后重抽间隔（RAD-023 扩展）。"""

from datetime import UTC, datetime, timedelta

from app.db.models import Creator, SourceAccount
from app.services import discovery


def _make_account(session, *, platform="douyin", external_id="MS4wLjABrand1", **over):
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


def test_random_interval_fields_defaults(db_session):
    account = _make_account(db_session)
    db_session.refresh(account)
    assert account.poll_interval_min_sec is None
    assert account.poll_interval_max_sec is None


def test_random_interval_roundtrip(db_session):
    account = _make_account(
        db_session, poll_interval_min_sec=300, poll_interval_max_sec=900
    )
    db_session.commit()
    db_session.expire_all()
    fresh = db_session.get(SourceAccount, account.id)
    assert (fresh.poll_interval_min_sec, fresh.poll_interval_max_sec) == (300, 900)


def test_redraw_pulls_interval_into_range(db_session):
    """重抽结果始终落在 [min, max] 内。"""
    fake = discovery.SourceAccount(
        creator_id=1,
        platform="douyin",
        external_id="x",
        poll_interval_sec=3600,
        poll_interval_min_sec=300,
        poll_interval_max_sec=900,
    )
    seen = set()
    for _ in range(50):
        discovery._redraw_random_poll_interval(fake)
        assert 300 <= fake.poll_interval_sec <= 900
        seen.add(fake.poll_interval_sec)
    assert len(seen) > 1  # 确实在随机，不是恒定值


def test_redraw_noop_without_range(db_session):
    """未配置区间的账号保持固定间隔（旧行为兼容）。"""
    account = discovery.SourceAccount(
        creator_id=1,
        platform="douyin",
        external_id="x",
        poll_interval_sec=1800,
    )
    for _ in range(10):
        discovery._redraw_random_poll_interval(account)
    assert account.poll_interval_sec == 1800


def test_redraw_noop_for_invalid_range():
    """min>max 或非正值时不动间隔，防御脏配置。"""
    for lo, hi in ((900, 300), (0, 900), (-5, 100)):
        account = discovery.SourceAccount(
            creator_id=1,
            platform="douyin",
            external_id="x",
            poll_interval_sec=1800,
            poll_interval_min_sec=lo,
            poll_interval_max_sec=hi,
        )
        discovery._redraw_random_poll_interval(account)
        assert account.poll_interval_sec == 1800


def test_discover_success_redraws_interval(db_session, monkeypatch):
    """discover_account 成功路径整体生效：last_success_at 推进 + 间隔被重抽进区间。"""
    from unittest.mock import MagicMock

    from app.services.media.contracts import DiscoveredItem

    account = _make_account(
        db_session,
        poll_interval_sec=3600,
        poll_interval_min_sec=300,
        poll_interval_max_sec=900,
        discovery_mode="auto_poll",  # 开视频监控才会自动投递转录
    )
    old_success = datetime.now(UTC) - timedelta(hours=2)
    account.last_success_at = old_success
    db_session.commit()

    adapter = MagicMock()
    adapter.discover.return_value = [
        DiscoveredItem(
            external_item_id="aweme_1",
            title="最新视频",
            url="https://www.douyin.com/video/1",
            published_at=None,
            duration_ms=60000,
            metadata={},
        )
    ]
    sent = []
    result = discovery.discover_account(
        account.id, db_session, adapter, send=lambda name, args=None, **kw: sent.append((name, args))
    )
    assert result["created"] == 1
    db_session.expire_all()
    fresh = db_session.get(SourceAccount, account.id)
    assert fresh.last_success_at is not None and fresh.last_success_at > old_success
    assert 300 <= fresh.poll_interval_sec <= 900
    assert sent == [("prepare_source_item", [fresh.id])]
