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
    account = _make_account(db_session, external_id="ch_a", url="https://www.youtube.com/@a/videos")
    db_session.commit()  # 任务用独立 session，先落库

    outcome = worker_tasks.discover_source_account.run(account.id)
    assert outcome["created"] == 1
    assert db_session.query(SourceItem).filter(SourceItem.external_item_id == "v1").count() == 1
    assert sent == [("prepare_source_item", {"args": [1]})]


def test_dispatch_due_only_enabled_and_due(db_session, sent, task_session_factory):
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

    n = worker_tasks.dispatch_due_discoveries.run()
    assert n == 1
    assert sent == [("discover_source_account", {"args": [due.id]})]

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
