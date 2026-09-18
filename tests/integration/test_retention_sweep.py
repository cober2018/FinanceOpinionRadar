"""retention sweep 集成测试（Plan #6 Task 2）：到期清理/结论快照/墓碑/精华豁免。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.db.models import (
    ContentSummary,
    Creator,
    DeletedItemRef,
    LiveChatMessage,
    SourceAccount,
    SourceItem,
    Viewpoint,
)
from app.services.retention import sweep_expired_content

OLD = datetime.now(UTC) - timedelta(days=60)  # 远超默认 30 天 TTL


def _settings(tmp_path: Path, **over):
    base = {
        "content_retention_days": 30,
        "danmaku_sink_dir": str(tmp_path / "sink"),
    }
    base.update(over)
    return SimpleNamespace(**base)


def _make_item(
    db_session,
    *,
    external_id="MS4wLjABret1",
    status="transcribed",
    created_at=OLD,
    is_asset=False,
    item_type="vod",
    with_viewpoint=True,
    with_chat=False,
):
    creator = db_session.query(Creator).filter(Creator.display_name == f"c_{external_id}").one_or_none()
    if creator is None:
        creator = Creator(display_name=f"c_{external_id}", status="active")
        db_session.add(creator)
        db_session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=external_id,
        discovery_mode="manual",
        poll_interval_sec=3600,
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=f"{item_type}:{external_id}:2026-09-19",
        item_type=item_type,
        title=f"测试条目 {external_id}",
        status=status,
        created_at=created_at,
        is_asset=is_asset,
    )
    db_session.add(item)
    db_session.flush()
    if with_viewpoint:
        db_session.add(
            Viewpoint(
                creator_id=creator.id,
                source_item_id=item.id,
                claim="降息周期利好黄金",
                stance="bullish",
                importance=0.7,
                confidence=0.8,
            )
        )
    if with_chat:
        db_session.add(
            LiveChatMessage(
                source_item_id=item.id,
                msg_type="WebcastChatMessage",
                external_msg_id=f"{item.id}-m1",
                user_name="测试观众",
                text="弹幕内容",
            )
        )
    db_session.commit()
    return item


def test_sweep_deletes_expired_and_snapshots_viewpoints(db_session, tmp_path):
    item = _make_item(db_session, with_chat=True)
    out = sweep_expired_content(db_session, settings=_settings(tmp_path))
    assert out["expired"] == 1 and out["swept"] == 1 and out["snapshots"] == 1
    assert out["tombstones"] == 1
    # 物理删除（transcript/弹幕/观点级联）
    assert db_session.get(SourceItem, item.id) is None
    assert db_session.query(LiveChatMessage).count() == 0
    # 结论快照留存且与观点一致
    summary = db_session.query(ContentSummary).one()
    assert summary.creator_name == "c_MS4wLjABret1"
    assert summary.item_title == "测试条目 MS4wLjABret1"
    assert len(summary.viewpoints) == 1
    vp = summary.viewpoints[0]
    assert vp["claim"] == "降息周期利好黄金"
    assert vp["stance"] == "bullish"
    assert float(vp["confidence"]) == 0.8
    # 墓碑防重导
    assert db_session.query(DeletedItemRef).count() == 1


def test_sweep_keeps_recent_and_asset(db_session, tmp_path):
    fresh = _make_item(
        db_session, external_id="MS4wLjABret2", created_at=datetime.now(UTC) - timedelta(days=1)
    )
    asset = _make_item(
        db_session, external_id="MS4wLjABret3", is_asset=True, with_viewpoint=False
    )
    out = sweep_expired_content(db_session, settings=_settings(tmp_path))
    assert out["expired"] == 0 and out["swept"] == 0
    assert db_session.get(SourceItem, fresh.id) is not None
    assert db_session.get(SourceItem, asset.id) is not None


def test_sweep_covers_failed_reviewing_ready_not_discovered(db_session, tmp_path):
    failed = _make_item(db_session, external_id="MS4wLjABret4", status="failed", with_viewpoint=False)
    reviewing = _make_item(db_session, external_id="MS4wLjABret5", status="reviewing")
    ready = _make_item(db_session, external_id="MS4wLjABret6", status="ready")
    discovered = _make_item(
        db_session,
        external_id="MS4wLjABret7",
        status="discovered",
        with_viewpoint=False,
        created_at=OLD,
    )
    out = sweep_expired_content(db_session, settings=_settings(tmp_path))
    assert out["swept"] == 3
    for i in (failed, reviewing, ready):
        assert db_session.get(SourceItem, i.id) is None
    assert db_session.get(SourceItem, discovered.id) is not None  # 待转写保留
    assert db_session.query(ContentSummary).count() == 3  # 无观点条目也留档（viewpoints=[]）


def test_sweep_dry_run_deletes_nothing(db_session, tmp_path):
    item = _make_item(db_session)
    out = sweep_expired_content(db_session, settings=_settings(tmp_path), dry_run=True)
    assert out["expired"] == 1 and out["swept"] == 0
    assert db_session.get(SourceItem, item.id) is not None
    assert db_session.query(ContentSummary).count() == 0


def test_sweep_deletes_sink_jsonl(db_session, tmp_path):
    item = _make_item(db_session, item_type="live", with_viewpoint=False, with_chat=False)
    sink = tmp_path / "sink" / "2026-09-19" / f"{item.id}.jsonl"
    sink.parent.mkdir(parents=True)
    sink.write_text("{}\n")
    sweep_expired_content(db_session, settings=_settings(tmp_path))
    assert not sink.exists()


def test_sweep_disabled_when_retention_zero(db_session, tmp_path):
    item = _make_item(db_session)
    out = sweep_expired_content(
        db_session, settings=_settings(tmp_path, content_retention_days=0)
    )
    assert "noop" in out
    assert db_session.get(SourceItem, item.id) is not None


def test_sweep_tombstone_collision_safe(db_session, tmp_path):
    """已手工删除过的 (account, external_id) 再被重导又过期：墓碑撞车不炸。"""
    item = _make_item(db_session, external_id="MS4wLjABret8", with_viewpoint=False)
    db_session.add(
        DeletedItemRef(
            source_account_id=item.source_account_id,
            external_item_id=item.external_item_id,
        )
    )
    db_session.commit()
    out = sweep_expired_content(db_session, settings=_settings(tmp_path))
    assert out["swept"] == 1 and out["tombstones"] == 0  # 跳过重复墓碑，不抛 IntegrityError
    assert db_session.get(SourceItem, item.id) is None


def test_sweep_respects_manual_status_filter_via_raw(db_session, tmp_path):
    """transcribing（直播进行中）绝不清。"""
    item = _make_item(
        db_session, external_id="MS4wLjABret9", status="transcribing", with_viewpoint=False
    )
    sweep_expired_content(db_session, settings=_settings(tmp_path))
    assert db_session.get(SourceItem, item.id) is not None
