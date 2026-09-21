"""Celery 任务直调（task.run()）验证编排与 DB 副作用；broker 交互不在本层测。"""

from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import Creator, SourceAccount, SourceItem
from app.repositories.source_accounts import SourceAccountRepository
from app.worker import tasks as worker_tasks
from app.worker.celery_app import celery_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.integration.test_discovery_service import StubAdapter


def _make_account(session, *, external_id, url=None, enabled=True, last_success=None):
    creator = Creator(display_name=f"作者{external_id}", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="youtube",
        external_id=external_id,
        url=url,
        enabled=enabled,
        last_success_at=last_success,
        discovery_mode="auto_poll",
        poll_interval_sec=3600,
    )
    session.add(account)
    session.flush()
    return account


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: calls.append((name, kw)))
    return calls


@pytest.fixture
def task_session_factory(monkeypatch: pytest.MonkeyPatch, database_url: str):
    """任务的 session 指向 radar_test 而非开发库（lru_cache 绕不开，直接换函数）。"""
    factory = sessionmaker(bind=create_engine(database_url), expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "get_session_factory", lambda: factory)
    return factory


@pytest.fixture
def stub_adapter(monkeypatch: pytest.MonkeyPatch):
    from app.services.media.contracts import DiscoveredItem

    items = [
        DiscoveredItem("v1", "一", "https://www.youtube.com/watch?v=v1", None, None, {}),
    ]
    adapter = StubAdapter(discovered=items)
    monkeypatch.setattr(worker_tasks, "build_adapter", lambda platform=None: adapter)
    return adapter


def test_discover_source_account_task_runs_end_to_end(
    db_session, stub_adapter, sent, task_session_factory
):
    account = _make_account(
        db_session,
        external_id="ch_a",
        url="https://www.youtube.com/@a/videos",
        last_success=datetime.now(UTC) - timedelta(hours=2),  # 非首扫：跟踪新视频才自动转录
    )
    db_session.commit()  # 任务用独立 session，先落库

    outcome = worker_tasks.discover_source_account.run(account.id)
    assert outcome["created"] == 1
    assert db_session.query(SourceItem).filter(SourceItem.external_item_id == "v1").count() == 1
    assert sent == [("prepare_source_item", {"args": [1]})]


def test_dispatch_due_only_enabled_and_due(
    db_session, sent, task_session_factory, monkeypatch: pytest.MonkeyPatch
):
    due = _make_account(db_session, external_id="due", url="https://www.youtube.com/@d/videos")
    _make_account(
        db_session,
        external_id="fresh",
        url="https://www.youtube.com/@f/videos",
        last_success=datetime.now(UTC) - timedelta(seconds=10),  # 刚成功过，未到期
    )
    _make_account(
        db_session,
        external_id="off",
        enabled=False,
        url="https://www.youtube.com/@o/videos",
    )
    db_session.commit()

    # 封闭 .env：stagger 显式 0 → payload 无 countdown（桩打在 live_status 的 env 读取点）
    from types import SimpleNamespace

    from app.services import live_status

    # dispatch 优先读 DB 安全设置（get_security_settings），env 只是 None 回落——
    # 会话内其他用例可能写过 security 覆盖行，这里直接桩掉读取点保证确定性
    def _security_with(stagger: int):
        return lambda session: {"effective": {"discover_dispatch_stagger_max_sec": stagger}}

    monkeypatch.setattr(live_status, "get_security_settings", _security_with(0))
    n = worker_tasks.dispatch_due_discoveries.run()
    assert n == 1
    assert sent == [("discover_source_account", {"args": [due.id]})]

    # stagger>0：payload 带 0~N 随机 countdown（人类化错峰）
    monkeypatch.setattr(live_status, "get_security_settings", _security_with(60))
    sent.clear()
    worker_tasks.dispatch_due_discoveries.run()
    ((name, kwargs),) = sent
    assert name == "discover_source_account" and kwargs["args"] == [due.id]
    assert 0 <= kwargs["countdown"] <= 60

    # repo 层三态单测（E5）
    repo = SourceAccountRepository(db_session)
    due_ids = [a.id for a in repo.list_due()]
    assert due_ids == [due.id]


def test_failure_marks_task_failed(db_session, monkeypatch, task_session_factory):
    from app.services.media.contracts import AdapterError

    account = _make_account(db_session, external_id="bad", url="https://www.youtube.com/@b/videos")
    db_session.commit()
    monkeypatch.setattr(
        worker_tasks,
        "build_adapter",
        lambda platform=None: StubAdapter(discovered=AdapterError("yt-dlp 崩了")),
    )
    with pytest.raises(AdapterError):
        worker_tasks.discover_source_account.run(account.id)
    db_session.rollback()
    refreshed = db_session.get(SourceAccount, account.id)
    assert refreshed.failure_count == 1


def test_beat_schedule_wired():
    # GAP-4：beat 接线断言
    sched = celery_app.conf.beat_schedule.get("dispatch-due-discoveries")
    assert sched is not None and sched["task"] == "dispatch_due_discoveries"
    assert sched["schedule"] > 0


def test_dispatch_includes_auto_poll_accounts(db_session, task_session_factory, sent):
    """CEO F1 语义：enabled 即参与轮询，auto_poll 是意图标注——auto_poll 账号必须被派发。"""
    account = _make_account(
        db_session,
        external_id="auto_poll_acc",
        url="https://www.douyin.com/user/MS4wLjABauto",
    )
    account.discovery_mode = "auto_poll"
    account.poll_interval_sec = 1800
    db_session.commit()

    worker_tasks.dispatch_due_discoveries.run()
    assert any(c[1]["args"][0] == account.id for c in sent)


def test_first_scan_backfill_titles_only_second_scan_auto_prepares(
    db_session, stub_adapter, sent, task_session_factory
):
    """机制语义：首扫（回溯）只采标题（backfill 标记、不投转录）；
    二扫起的新视频仅 auto_poll 账号自动转录；manual 账号永不自动转录。"""
    account = _make_account(
        db_session, external_id="fresh_chan", url="https://www.youtube.com/@fresh/videos"
    )
    db_session.commit()

    # 首扫：last_success_at 为 NULL → backfill
    worker_tasks.discover_source_account.run(account.id)
    assert sent == []  # 不投递转录
    item = db_session.query(SourceItem).filter(SourceItem.external_item_id == "v1").one()
    assert item.metadata_json["backfill"] is True

    # 二扫：无新条目，也不补投 backfill 条目
    db_session.expire_all()
    worker_tasks.discover_source_account.run(account.id)
    assert sent == []

    # 账号开视频监控（auto_poll）后，二扫无新条目 → 依旧无投递（backfill 条目不补投）
    account.discovery_mode = "auto_poll"
    db_session.commit()
    db_session.expire_all()
    worker_tasks.discover_source_account.run(account.id)
    assert sent == []


def test_retry_failed_prepares_backoff_and_cap(db_session, sent, task_session_factory):
    """自动重试：首次立即派发；间隔未到不派；5 次封顶。"""
    from datetime import UTC, datetime

    from app.db.models import Creator, SourceAccount, SourceItem

    creator = Creator(display_name="重试主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(creator_id=creator.id, platform="douyin", external_id="MS4wLjABretry1")
    db_session.add(account)
    db_session.flush()

    def _mk(external, retry_meta):
        it = SourceItem(
            source_account_id=account.id,
            external_item_id=external,
            item_type="vod",
            status="failed",
            metadata_json={"retry": retry_meta} if retry_meta else {},
        )
        db_session.add(it)
        return it

    fresh = _mk("v_fresh", None)  # 从未重试 → 立即派
    cooling = _mk("v_cooling", {"count": 1, "last_at": datetime.now(UTC).isoformat()})  # 间隔未到
    capped = _mk("v_capped", {"count": 5, "last_at": "2026-09-18T00:00:00+00:00"})  # 封顶
    db_session.commit()

    import app.worker.tasks as wt

    wt.get_session_factory = lambda: task_session_factory
    n = wt.retry_failed_prepares.run()
    assert n == 1
    assert [args for _, kw in sent for args in [kw.get("args")]] == [[fresh.id]]
    assert cooling.id not in [i for kw_args in [kw.get("args") for _, kw in sent] for i in kw_args]
    assert capped.id not in [i for kw_args in [kw.get("args") for _, kw in sent] for i in kw_args]
    db_session.expire_all()
    assert (db_session.get(SourceItem, fresh.id).metadata_json or {}).get("retry", {}).get("count") == 1
