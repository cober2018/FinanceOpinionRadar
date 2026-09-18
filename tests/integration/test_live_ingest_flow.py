"""live ingest 集成流（Plan #4 Task 5 Step 2）：真 PG + fake 外部依赖。

覆盖：会话建立与状态链（F3）、偏移累加、幂等（E2 闸外）、单飞闸（E2/G3）、
跨零点合并（F2）、transcript 追加（E3 复用既有写入函数）。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.db.models import Creator, SourceAccount, SourceItem, TranscriptSegment
from app.services import live_ingest
from app.services.media.audio import NormalizedAudio
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

SEG_MS = 60_000  # 每分片 60s


def _settings(tmp_path):
    return SimpleNamespace(
        live_segments_dir=str(tmp_path),
        live_close_grace_sec=900,
        live_min_segment_sec=30,
        live_max_segments_per_session=120,
        live_segment_max_attempts=3,
    )


def _fake_normalize(duration_ms: int = SEG_MS):
    def _norm(input_path: Path, output_dir: Path, *, binary="ffmpeg", timeout_sec=600):
        out = output_dir / (Path(input_path).stem + ".16k-mono.wav")
        out.write_bytes(b"RIFF-fake")
        return NormalizedAudio(
            path=out,
            input_sha256="i" * 64,
            output_sha256="o" * 64,
            duration_ms=duration_ms,
        )

    return _norm


def _fake_provider():
    provider = Mock()
    call_no = {"n": 0}

    def _transcribe(path, language=None):
        call_no["n"] += 1
        return SimpleNamespace(
            language="zh",
            provider="fake",
            model="fake",
            segments=(
                SimpleNamespace(
                    start_ms=0, end_ms=500, text=f"段{call_no['n']}", confidence=0.9
                ),
            ),
        )

    provider.transcribe.side_effect = _transcribe
    return provider


@pytest.fixture
def flow(db_session, database_url, monkeypatch: pytest.MonkeyPatch, tmp_path):
    """任务的 session 换绑测试库（同 test_prepare_tasks 手法）+ 显式预建直播账号
    （2026-09-18 起 ingest 不再自动建档：目录无对应账号即跳过，防删除后复活）。"""
    creator = Creator(display_name="新闻联播", status="active")
    db_session.add(creator)
    db_session.flush()
    db_session.add(
        SourceAccount(
            creator_id=creator.id,
            platform="douyin",
            external_id="新闻联播",
            enabled=False,
        )
    )
    db_session.commit()
    factory = sessionmaker(bind=create_engine(database_url), expire_on_commit=False)
    monkeypatch.setattr(live_ingest, "get_session_factory", lambda: factory)
    monkeypatch.setattr(live_ingest, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(live_ingest, "normalize_audio", _fake_normalize())
    holder = SimpleNamespace(factory=factory, tmp_path=tmp_path)
    return holder


def _write_seg(root: Path, author: str, date: str, base: str, index: int) -> Path:
    d = root / author / date
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{base}_{index:03d}.TS"
    p.write_bytes(b"\x00" * 16)
    return p


def _set_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(live_ingest, "get_settings", lambda: _settings(tmp_path))


def _make_tree(root: Path) -> Path:
    d = root / "新闻联播" / "2026-09-17"
    d.mkdir(parents=True)
    return d


def _ensure_watch_account(db_session) -> None:
    """预建直播账号（ingest 不再自动建档）。幂等。"""
    if (
        db_session.query(SourceAccount)
        .filter_by(platform="douyin", external_id="新闻联播")
        .one_or_none()
    ):
        return
    creator = Creator(display_name="新闻联播", status="active")
    db_session.add(creator)
    db_session.flush()
    db_session.add(
        SourceAccount(creator_id=creator.id, platform="douyin", external_id="新闻联播", enabled=False)
    )
    db_session.commit()


def _live_item(db_session) -> SourceItem:
    return db_session.query(SourceItem).filter(SourceItem.item_type == "live").one()


def _provider_of(flow):
    # 每轮共用一个 provider，便于断言调用次数
    if not hasattr(flow, "provider"):
        flow.provider = _fake_provider()
    return flow.provider


def test_full_flow_creates_session_and_accumulates_offset(flow, db_session):
    root = flow.tmp_path
    provider = _provider_of(flow)
    for i in range(2):
        _write_seg(root, "新闻联播", "2026-09-17", "x20260917x", i)

    live_ingest.ingest_live_segments(db_session, provider=provider)
    db_session.expire_all()

    item = (
        db_session.query(SourceItem).filter(SourceItem.item_type == "live").one()
    )
    assert item.status == "transcribing"  # F3：连续推进后停驻
    live_meta = item.metadata_json["live"]
    assert live_meta["processed"].keys() == {"0", "1"}
    assert live_meta["segment_count"] == 2

    # 偏移：seg1 起点 = seg0 时长（F5：以已处理分片实际时长为准）
    segs = (
        db_session.query(TranscriptSegment)
        .filter(TranscriptSegment.source_item_id == item.id)
        .order_by(TranscriptSegment.sequence_no)
        .all()
    )
    assert [s.start_ms for s in segs] == [0, SEG_MS]
    assert [s.sequence_no for s in segs] == [0, 1]
    assert segs[1].text == "段2"  # E3 追加而非覆盖

    # 账号来自显式预建（ingest 不再自动建档）：enabled=False，不入发现轮询
    account = db_session.get(SourceAccount, item.source_account_id)
    assert account.enabled is False
    assert account.external_id == "新闻联播"


def test_rerun_same_dir_is_idempotent(flow, db_session):
    root = flow.tmp_path
    provider = _provider_of(flow)
    _write_seg(root, "新闻联播", "2026-09-17", "x20260917x", 0)

    live_ingest.ingest_live_segments(db_session, provider=provider)
    live_ingest.ingest_live_segments(db_session, provider=provider)
    db_session.expire_all()

    item = (
        db_session.query(SourceItem).filter(SourceItem.item_type == "live").one()
    )
    assert provider.transcribe.call_count == 1  # 幂等跳出
    assert (
        db_session.query(TranscriptSegment)
        .filter(TranscriptSegment.source_item_id == item.id)
        .count()
        == 1
    )


def test_registry_persists_under_worker_session_options(flow):
    """worker 的 get_session_factory 是 expire_on_commit=False：内层 commit 后重赋
    同一 dict 对象不标脏，注册表从未落库（Task 6-Step3 真栈抓到的 Task 5 缺陷）。
    模拟 beat 两轮：每轮用工厂开全新 session，注册表必须真正持久化。"""
    root, provider = flow.tmp_path, _provider_of(flow)
    _write_seg(root, "新闻联播", "2026-09-17", "x20260917x", 0)

    with flow.factory() as first_round:
        live_ingest.ingest_live_segments(first_round, provider=provider)
    with flow.factory() as verify:
        item = verify.query(SourceItem).filter(SourceItem.item_type == "live").one()
        assert item.metadata_json["live"]["processed"].keys() == {"0"}
        assert item.metadata_json["live"]["segment_count"] == 1

    # 第二轮：同分片幂等跳出，不重复转写
    with flow.factory() as second_round:
        result = live_ingest.ingest_live_segments(second_round, provider=provider)
        assert result["segments_pending"] == 0
    assert _provider_of(flow).transcribe.call_count == 1


def test_cross_midnight_creates_new_dir_but_extends_open_session(flow, db_session):
    """F2（Task 1 核对项①）：跨零点拆目录，会话身份以未收尾优先归并。"""
    root = flow.tmp_path
    provider = _provider_of(flow)
    _write_seg(root, "新闻联播", "2026-09-17", "x17", 0)
    live_ingest.ingest_live_segments(db_session, provider=provider)

    _write_seg(root, "新闻联播", "2026-09-18", "x18", 1)
    live_ingest.ingest_live_segments(db_session, provider=provider)
    db_session.expire_all()

    items = (
        db_session.query(SourceItem).filter(SourceItem.item_type == "live").all()
    )
    assert len(items) == 1  # 未收尾 → 延续既有 item 而非新建
    assert items[0].external_item_id == "live:新闻联播:2026-09-17"
    assert items[0].metadata_json["live"]["processed"].keys() == {"0", "1"}


def test_singleton_lock_rejects_concurrent_ingest(flow, db_session, database_url):
    """E2/G3：抢不到单飞闸立即退出。"""
    root = flow.tmp_path
    _write_seg(root, "新闻联播", "2026-09-17", "x20260917x", 0)

    holder = sessionmaker(bind=create_engine(database_url))
    with holder() as locker:
        got = locker.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": live_ingest._SINGLETON_KEY}
        ).scalar()
        assert got is True
        try:
            result = live_ingest.ingest_live_segments(db_session)
            assert result["skipped"] == "singleton"
        finally:
            locker.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": live_ingest._SINGLETON_KEY}
            )


def test_max_segments_per_session_stops_scan(flow, db_session, monkeypatch):
    root = flow.tmp_path
    for i in range(3):
        _write_seg(root, "新闻联播", "2026-09-17", "x20260917x", i)
    monkeypatch.setattr(
        live_ingest,
        "get_settings",
        lambda: SimpleNamespace(
            live_segments_dir=str(root),
            live_close_grace_sec=900,
            live_min_segment_sec=30,
            live_max_segments_per_session=2,
            live_segment_max_attempts=3,
        ),
    )
    live_ingest.ingest_live_segments(db_session, provider=_provider_of(flow))
    db_session.expire_all()
    item = (
        db_session.query(SourceItem).filter(SourceItem.item_type == "live").one()
    )
    assert item.metadata_json["live"]["processed"].keys() == {"0", "1"}
    assert "2" in item.metadata_json["live"]["deferred"]


# --- 从 unit 归并（需真库：conftest 作用域） ---


def test_ingest_noop_when_dir_unconfigured(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    # 显式置空而非依赖环境：本机 .env 可能配置 LIVE_SEGMENTS_DIR（真栈验收）
    _set_dir(monkeypatch, "")
    result = live_ingest.ingest_live_segments(db_session)
    assert result["sessions_active"] == 0
    assert result["noop"] == "live_segments_dir 未配置"


def test_ingest_warns_when_dir_missing(db_session, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _set_dir(monkeypatch, tmp_path / "missing")
    result = live_ingest.ingest_live_segments(db_session)
    assert result["sessions_active"] == 0


def test_min_duration_segment_skipped_not_counted_in_offset(
    db_session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """F5：残片跳过且不计入偏移。"""
    _ensure_watch_account(db_session)
    _write_seg(tmp_path, "新闻联播", "2026-09-17", "base", 0)
    _set_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(live_ingest, "normalize_audio", _fake_normalize(1_000))  # <30s

    result = live_ingest.ingest_live_segments(db_session, provider=_fake_provider())
    item = _live_item(db_session)
    assert item.metadata_json["live"]["processed"] == {}
    assert result["segments_pending"] == 0
    assert item.status == "transcribing"  # 会话已建、停驻 transcribing（F3）


def test_segment_retry_cap_skips_after_max_attempts(
    db_session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """F4：同分片连续失败 ≥max 后跳过记账，不再重试。"""
    _ensure_watch_account(db_session)
    _write_seg(tmp_path, "新闻联播", "2026-09-17", "base", 0)
    _set_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(live_ingest, "normalize_audio", _fake_normalize(60_000))

    bad_provider = Mock()
    bad_provider.transcribe.side_effect = RuntimeError("模型炸了")

    for _ in range(3):
        live_ingest.ingest_live_segments(db_session, provider=bad_provider)
    result = live_ingest.ingest_live_segments(db_session, provider=bad_provider)

    live_meta = _live_item(db_session).metadata_json["live"]
    assert live_meta["segment_errors"]["0"]["attempts"] == 3
    # 第 4 轮不再尝试：provider 调用次数停在 3
    assert bad_provider.transcribe.call_count == 3
    assert result["segments_failed"] == 0


def test_session_close_after_grace(
    db_session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """G1：静默超过 grace → 会话收尾 transcribing→transcribed。"""
    _ensure_watch_account(db_session)
    _write_seg(tmp_path, "新闻联播", "2026-09-17", "base", 0)
    _set_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(live_ingest, "normalize_audio", _fake_normalize(60_000))

    live_ingest.ingest_live_segments(db_session, provider=_fake_provider())
    item = _live_item(db_session)
    assert item.status == "transcribing"

    stale = datetime.now(UTC) - timedelta(seconds=3600)
    meta = dict(item.metadata_json)
    live_meta = dict(meta["live"])
    live_meta["last_segment_at"] = stale.isoformat()
    meta["live"] = live_meta
    item.metadata_json = meta
    db_session.commit()

    result = live_ingest.ingest_live_segments(db_session, provider=_fake_provider())
    db_session.expire_all()
    item = db_session.get(SourceItem, item.id)
    assert item.status == "transcribed"
    assert item.metadata_json["live"]["closed"] is True
    assert result["sessions_active"] == 0
