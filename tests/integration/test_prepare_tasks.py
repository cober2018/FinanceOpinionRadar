"""prepare_source_item / dispatch_pending_prepares 任务直调（绕 broker），真 DB + fake 外部依赖。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from app.db.models import SourceItem
from app.services import preparation
from app.services.media.audio import NormalizedAudio
from app.worker import tasks as worker_tasks
from app.worker.celery_app import celery_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.integration.test_preparation_service import (
    FakeAdapter,
    FakeProvider,
    FakeStorage,
    _make_discovered_item,
    _resolved,
)


@pytest.fixture
def task_session_factory(monkeypatch: pytest.MonkeyPatch, database_url: str):
    """任务的 session 指向 radar_test 而非开发库（同 test_discover_tasks 的换绑手法）。"""
    factory = sessionmaker(bind=create_engine(database_url), expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "get_session_factory", lambda: factory)
    return factory


def _fake_normalize(input_path, output_dir, *, binary="ffmpeg", timeout_sec=600):
    out = Path(output_dir) / "abc123.16k-mono.wav"
    out.write_bytes(b"RIFF-fake-wav")
    return NormalizedAudio(
        path=out, input_sha256="i" * 64, output_sha256="o" * 64, duration_ms=1250
    )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: calls.append((name, kw)))
    return calls


def test_prepare_task_runs_pipeline(db_session, task_session_factory, monkeypatch):
    item = _make_discovered_item(db_session)
    db_session.commit()  # 任务用独立 session，先落库
    monkeypatch.setattr(worker_tasks, "build_adapter", lambda platform=None, **kw: FakeAdapter(_resolved()))
    monkeypatch.setattr("app.services.storage.get_storage", lambda: FakeStorage())
    monkeypatch.setattr(
        "app.services.transcription.get_transcription_provider", lambda: FakeProvider()
    )
    monkeypatch.setattr(preparation, "normalize_audio", _fake_normalize)

    result = worker_tasks.prepare_source_item.run(item.id)
    assert result["status"] == "transcribed" and result["origin"] == "asr"
    db_session.expire_all()
    item = db_session.get(SourceItem, item.id)
    assert item.status == "transcribed"

    # 成功即清进度：progress 只服务进行中/失败态，成功后不残留进行时文案
    prog = (item.metadata_json or {}).get("progress")
    assert not prog, prog


def test_sweep_dispatches_discovered_only(db_session, task_session_factory, sent):
    first = _make_discovered_item(db_session, external_id="v1")
    second = _make_discovered_item(db_session, external_id="v2")
    done = _make_discovered_item(db_session, external_id="v3")
    done.status = "transcribed"  # 已完成的不再派发
    db_session.commit()

    n = worker_tasks.dispatch_pending_prepares.run()
    assert n == 2
    assert {c[1]["args"][0] for c in sent} == {first.id, second.id}
    assert {c[0] for c in sent} == {"prepare_source_item"}


def test_sweep_respects_batch_size(db_session, task_session_factory, sent, monkeypatch):
    items = [
        _make_discovered_item(db_session, external_id=f"v{i}") for i in range(1, 4)
    ]
    db_session.commit()
    fake_settings = SimpleNamespace(prepare_sweep_batch_size=2, prepare_sweep_interval_sec=600)
    # get_settings 是 tasks.py 顶部绑定名，patch 源模块无效，须 patch worker 命名空间
    monkeypatch.setattr(worker_tasks, "get_settings", lambda: fake_settings)

    n = worker_tasks.dispatch_pending_prepares.run()
    assert n == 2
    assert [c[1]["args"][0] for c in sent] == sorted(x.id for x in items)[:2]  # id 升序截断


# --- Plan #4 Task 2 Step 5：per-platform 分流（F8） ---


def _make_douyin_discovered_item(session):
    from app.db.models import Creator
    from app.db.models.source import SourceAccount
    from app.repositories.source_items import SourceItemRepository

    creator = Creator(display_name="抖音博主", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id="MS4wLjABdytest",
        url="https://www.douyin.com/user/MS4wLjABdytest",
        discovery_mode="manual",
        poll_interval_sec=3600,
    )
    session.add(account)
    session.flush()
    item, _created = SourceItemRepository(session).upsert_by_external(
        source_account_id=account.id,
        external_item_id="dy001",
        title="抖音视频",
        canonical_url="https://www.douyin.com/video/dy001",
        item_type="vod",
    )
    session.flush()
    return item


def test_prepare_task_routes_douyin_platform(db_session, task_session_factory, monkeypatch):
    """douyin 平台 item → build_adapter 收到 "douyin"（分流正确性）。"""
    item = _make_douyin_discovered_item(db_session)
    db_session.commit()
    seen: dict = {}

    def fake_build(platform=None, **kw):
        seen["platform"] = platform
        return FakeAdapter(_resolved())

    monkeypatch.setattr(worker_tasks, "build_adapter", fake_build)
    monkeypatch.setattr("app.services.storage.get_storage", lambda: FakeStorage())
    monkeypatch.setattr(
        "app.services.transcription.get_transcription_provider", lambda: FakeProvider()
    )
    monkeypatch.setattr(preparation, "normalize_audio", _fake_normalize)

    worker_tasks.prepare_source_item.run(item.id)
    assert seen["platform"] == "douyin"


def test_prepare_task_douyin_unconfigured_writes_last_error(
    db_session, task_session_factory, monkeypatch
):
    """douyin 未配置 dtk → 构造期 AdapterError 与其他 prepare 失败同语义落 last_error。"""
    from app.core.settings import get_settings
    from app.services.media.factory import get_media_adapter

    item = _make_douyin_discovered_item(db_session)
    db_session.commit()
    monkeypatch.setenv("DOUYIN_API_BASE_URL", "")
    get_settings.cache_clear()
    get_media_adapter.cache_clear()
    try:
        result = worker_tasks.prepare_source_item.run(item.id)
    finally:
        get_settings.cache_clear()
        get_media_adapter.cache_clear()

    assert result["code"] == "RESOLVE_FAILED" and result["status"] == "failed"
    db_session.expire_all()
    meta = db_session.get(SourceItem, item.id).metadata_json
    assert meta["last_error"]["code"] == "RESOLVE_FAILED"
    assert "DOUYIN_API_BASE_URL" in meta["last_error"]["message"]
