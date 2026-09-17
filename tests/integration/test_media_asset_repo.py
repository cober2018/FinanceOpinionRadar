"""MediaAssetRepository 集成测试（RAD-034）：真实 FK 行 + 落库/列举。"""

from app.db.models import MediaAsset
from app.repositories.media_assets import MediaAssetRepository
from app.repositories.source_items import SourceItemRepository

from tests.integration.test_discover_tasks import _make_account


def _make_item(db_session, external_id: str = "v1"):
    account = _make_account(db_session, external_id=f"acc_{external_id}")
    db_session.flush()
    item, _created = SourceItemRepository(db_session).upsert_by_external(
        source_account_id=account.id,
        external_item_id=external_id,
        title="测试条目",
        canonical_url=f"https://www.youtube.com/watch?v={external_id}",
        item_type="vod",
    )
    db_session.flush()
    return item


def test_record_and_list_media_assets(db_session) -> None:
    item = _make_item(db_session)
    repo = MediaAssetRepository(db_session)
    a1 = repo.record(
        item.id,
        asset_type="subtitle",
        storage_uri="s3://b/s",
        mime_type="application/json",
        size_bytes=10,
        sha256="x" * 64,
    )
    a2 = repo.record(
        item.id,
        asset_type="audio",
        storage_uri="s3://b/a.wav",
        sha256="y" * 64,
        duration_ms=1500,
    )
    db_session.flush()
    listed = repo.list_for_item(item.id)
    assert sorted(x.id for x in listed) == [a1.id, a2.id]
    audio = next(x for x in listed if x.asset_type == "audio")
    assert audio.duration_ms == 1500
    assert isinstance(audio, MediaAsset)
    assert audio.mime_type is None  # 可空字段透传 None


def test_empty_list_for_item_without_assets(db_session) -> None:
    item = _make_item(db_session, external_id="v2")
    assert MediaAssetRepository(db_session).list_for_item(item.id) == []
