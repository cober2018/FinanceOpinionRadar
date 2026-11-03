"""条目删除的 content 域入口：物理文件清理与重导墓碑。

供 workflows 删除用例（单条/批量）调用；只修改/flush 不自行 commit。
"""

from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from app.runtime.db.models import DeletedItemRef, MediaAsset

logger = structlog.get_logger(__name__)


def purge_item_files(session: Session, item) -> dict:
    """条目物理文件清理：MinIO 对象（音频/转录）+ 本地直播分片。失败不阻断删行。"""
    from app.runtime.storage.base import StorageError

    purged = {"s3": 0, "local": 0}
    uris = [
        uri
        for (uri,) in session.query(MediaAsset.storage_uri)
        .filter(MediaAsset.source_item_id == item.id)
        .all()
        if uri
    ]
    for uri in uris:
        if uri.startswith("s3://"):
            key = uri.partition("://")[2].partition("/")[2]
            if not key:
                continue
            try:
                from app.runtime.storage import get_storage

                get_storage().delete(key)
                purged["s3"] += 1
            except StorageError as exc:
                logger.warning(
                    "purge_s3_delete_failed", item_id=item.id, key=key[:80], error=str(exc)[:120]
                )
        else:  # 本地路径：直播转写临时音频多已不存在，静默跳过
            Path(uri).unlink(missing_ok=True)
            purged["local"] += 1
    live = (item.metadata_json or {}).get("live") or {}
    for p in (live.get("segment_paths") or {}).values():
        try:
            Path(p).unlink(missing_ok=True)
            purged["local"] += 1
        except OSError as exc:
            logger.warning("purge_segment_unlink_failed", item_id=item.id, error=str(exc)[:120])
    if purged["s3"] or purged["local"]:
        logger.info("purge_item_files_done", item_id=item.id, **purged)
    return purged


def tombstone_if_absent(
    session: Session, source_account_id: int | None, external_item_id: str
) -> None:
    """墓碑幂等：条目曾被删过又被 live ingest 重建时，同键墓碑已存在——跳过插入，
    不能撞 uq_deleted_item_ref 500（750 实录：删除一直失败）。"""
    if source_account_id is None:
        return
    exists = (
        session.query(DeletedItemRef.id)
        .filter_by(
            source_account_id=source_account_id, external_item_id=external_item_id
        )
        .first()
    )
    if exists is None:
        session.add(
            DeletedItemRef(
                source_account_id=source_account_id, external_item_id=external_item_id
            )
        )
