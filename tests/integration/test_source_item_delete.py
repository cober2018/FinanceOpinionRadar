"""删除端点墓碑幂等（750 实录）：条目曾被删又被 live 重建，同键墓碑已存在时不得 500。
手动删除物理文件清理（用户语义 2026-09-24：手动删的什么都不留——MinIO 对象+直播分片）。
"""

import pytest
from fastapi.testclient import TestClient

from app.db.models import Creator, DeletedItemRef, MediaAsset, SourceAccount, SourceItem
from app.db.session import get_db


@pytest.fixture
def client(db_session):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class FakeStorage:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


def _mk_item(db_session, external: str, *, metadata_json=None) -> SourceItem:
    creator = Creator(display_name="主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec1", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=external,
        item_type="live",
        status="ready",
        metadata_json=metadata_json,
    )
    db_session.add(item)
    db_session.commit()
    return item


def test_delete_with_existing_tombstone_succeeds(db_session, client):
    item = _mk_item(db_session, "live:sec1:2026-09-17")
    db_session.add(
        DeletedItemRef(source_account_id=item.source_account_id, external_item_id=item.external_item_id)
    )
    db_session.commit()

    r = client.delete(f"/api/v1/source-items/{item.id}")
    assert r.status_code == 200  # 不再撞 uq_deleted_item_ref
    assert db_session.query(SourceItem).count() == 0
    assert db_session.query(DeletedItemRef).count() == 1  # 墓碑不重复插入


def test_batch_delete_with_existing_tombstone(db_session, client):
    a = _mk_item(db_session, "live:sec1:2026-09-18")
    db_session.add(
        DeletedItemRef(source_account_id=a.source_account_id, external_item_id=a.external_item_id)
    )
    db_session.commit()

    r = client.post("/api/v1/source-items/batch-delete", json={"ids": [a.id]})
    assert r.status_code == 200 and r.json()["deleted"] == [a.id]
    assert db_session.query(SourceItem).count() == 0


def test_delete_purges_s3_and_local_files(db_session, client, monkeypatch, tmp_path):
    import json as jsonlib

    seg = tmp_path / "seg_001.ts"
    seg.write_text("ts")
    tmp_audio = tmp_path / "live-x.wav"
    tmp_audio.write_text("wav")
    item = _mk_item(
        db_session,
        "live:sec1:2026-09-19",
        metadata_json={"live": {"segment_paths": {"5": str(seg)}}},
    )
    for asset_type, uri in [
        ("audio", f"s3://radar-media/audio/{item.id}.wav"),
        ("transcript", f"s3://radar-media/transcripts/{item.id}.engine/mod.json"),
        ("audio", str(tmp_audio)),  # 直播转写临时音频（本地路径，多已不存在）
    ]:
        db_session.add(
            MediaAsset(
                source_item_id=item.id,
                asset_type=asset_type,
                storage_uri=uri,
                size_bytes=10,
            )
        )
    db_session.commit()

    fake = FakeStorage({"audio/x.wav": 1, "transcripts/y.json": 1})
    monkeypatch.setattr("app.services.storage.get_storage", lambda: fake)

    r = client.delete(f"/api/v1/source-items/{item.id}")
    assert r.status_code == 200
    assert sorted(fake.deleted) == [
        f"audio/{item.id}.wav",
        f"transcripts/{item.id}.engine/mod.json",
    ]
    assert not seg.exists()  # 直播分片一并物理删除
    assert not tmp_audio.exists()
    assert db_session.query(MediaAsset).count() == 0  # 行级联清零
    payload = jsonlib.loads(r.text)
    assert payload["deleted"] == item.id
