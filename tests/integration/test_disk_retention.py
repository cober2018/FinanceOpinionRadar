"""孤儿对象回收 + 直播分片保留（用户语义 2026-09-24）。

在册 = media_asset ∪ content_summary.transcript_refs；audio/ 孤儿清、transcripts/
孤儿默认保留（生命周期转录保护 + 历史孤儿防误删），显式 include_transcripts 才清。
"""

from types import SimpleNamespace

from app.db.models import ContentSummary, MediaAsset, SourceItem
from app.services.live_retention import sweep_live_segments
from app.services.orphan_sweep import sweep_orphan_media


class FakeStorage:
    def __init__(self, objects: dict[str, int]):
        self.objects = dict(objects)
        self.deleted: list[str] = []

    def iter_keys(self):
        yield from self.objects.items()

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


def test_orphan_sweep_audio_only_by_default(db_session):
    from datetime import UTC, datetime

    from app.db.models import Creator, SourceAccount

    creator = Creator(display_name="retention测试", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec_rt", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id="rt1",
        item_type="vod",
        status="ready",
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(
        MediaAsset(
            source_item_id=item.id,
            asset_type="audio",
            storage_uri="s3://b/audio/1.wav",
            size_bytes=1,
        )
    )
    db_session.add(
        ContentSummary(
            creator_name="c",
            item_type="vod",
            item_created_at=datetime.now(UTC),
            transcript_refs=["s3://b/transcripts/old/engine/t.json"],
        )
    )
    db_session.commit()

    st = FakeStorage(
        {
            "audio/1.wav": 10,  # 在册 → 保留
            "audio/2.wav": 20,  # 孤儿 → 删
            "transcripts/old/engine/t.json": 5,  # refs 保护 → 保留
            "transcripts/lost/e/m.json": 7,  # 孤儿 → 默认保留（防误删生命周期文本）
            "llm-runs/run.json": 3,  # 白名单外 → 永不动
        }
    )
    out = sweep_orphan_media(db_session, storage=st)
    assert st.deleted == ["audio/2.wav"]
    assert out["audio_deleted"] == 1 and out["transcript_kept"] == 1
    assert out["bytes_freed"] == 20


def test_orphan_sweep_include_transcripts_explicit(db_session):
    from datetime import UTC, datetime

    st = FakeStorage(
        {
            "audio/9.wav": 10,
            "transcripts/protected/e/t.json": 5,  # refs 保护不受 include 影响
            "transcripts/lost/e/m.json": 7,
        }
    )
    db_session.add(
        ContentSummary(
            creator_name="c",
            item_type="vod",
            item_created_at=datetime.now(UTC),
            transcript_refs=["s3://b/transcripts/protected/e/t.json"],
        )
    )
    db_session.commit()
    out = sweep_orphan_media(db_session, storage=st, include_transcripts=True)
    assert sorted(st.deleted) == ["audio/9.wav", "transcripts/lost/e/m.json"]
    assert out["transcript_deleted"] == 1


def test_live_segment_retention_deletes_old_and_empty_dirs(tmp_path):
    import os
    import time

    old = tmp_path / "secA" / "2026-09-01"
    old.mkdir(parents=True)
    f_old = old / "a_2026-09-01_00-00-00_001.ts"
    f_old.write_text("x" * 100)
    past = (time.time() - 8 * 86400, time.time() - 8 * 86400)
    os.utime(f_old, past)

    new_dir = tmp_path / "secB" / "2026-09-23"
    new_dir.mkdir(parents=True)
    f_new = new_dir / "b.ts"
    f_new.write_text("y" * 100)

    settings = SimpleNamespace(
        live_segments_dir=str(tmp_path), live_segment_retention_days=7
    )
    out = sweep_live_segments(settings=settings)
    assert out["files_deleted"] == 1 and out["bytes_freed"] == 100
    assert not f_old.exists()
    assert not old.parent.exists()  # 空主播目录自底向上清理
    assert f_new.exists() and new_dir.exists()

    # 禁用（天数=0）
    out2 = sweep_live_segments(
        settings=SimpleNamespace(
            live_segments_dir=str(tmp_path), live_segment_retention_days=0
        )
    )
    assert out2.get("noop")
