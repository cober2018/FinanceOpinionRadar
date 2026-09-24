"""孤儿对象回收（Plan #7 后续）：MinIO 里不在册的媒体文件清理。

在册 = media_asset.storage_uri ∪ content_summary.transcript_refs（生命周期清理
条目的转录保留引用，用户语义 2026-09-24）。白名单只扫 audio/ 前缀；transcripts/
仅在 include_transcripts=True（显式触发）时清理——历史孤儿（功能上线前的手动
删除残留）无来源标记，为不误删生命周期转录，默认保留（合计仅几 MB）。

llm-runs/ 等其他前缀一律不动。
"""

import structlog

from app.core.settings import get_settings
from app.db.models import ContentSummary, MediaAsset
from app.services.storage.base import StorageError

logger = structlog.get_logger(__name__)


def _s3_key(uri: str) -> str | None:
    if not uri or not uri.startswith("s3://"):
        return None
    return uri.partition("://")[2].partition("/")[2] or None


def sweep_orphan_media(session, *, include_transcripts: bool = False, storage=None) -> dict:
    """返回 {scanned, audio_deleted, transcript_deleted, transcript_kept, bytes_freed, errors}。"""
    if storage is None:
        from app.services.storage import get_storage

        storage = get_storage()

    referenced: set[str] = set()
    for (uri,) in session.query(MediaAsset.storage_uri).all():
        key = _s3_key(uri)
        if key:
            referenced.add(key)
    for (refs,) in session.query(ContentSummary.transcript_refs).all():
        for uri in refs or []:
            key = _s3_key(uri)
            if key:
                referenced.add(key)

    counters = {
        "scanned": 0,
        "audio_deleted": 0,
        "transcript_deleted": 0,
        "transcript_kept": 0,
        "bytes_freed": 0,
        "errors": 0,
    }
    for key, size in list(storage.iter_keys()):
        counters["scanned"] += 1
        if key in referenced:
            continue
        if key.startswith("transcripts/"):
            if not include_transcripts:
                counters["transcript_kept"] += 1
                continue
        elif not key.startswith("audio/"):
            continue  # 白名单外（llm-runs/ 等）不动
        try:
            storage.delete(key)
        except StorageError as exc:
            counters["errors"] += 1
            logger.warning("orphan_sweep_delete_failed", key=key[:100], error=str(exc)[:120])
            continue
        counters["bytes_freed"] += size
        if key.startswith("transcripts/"):
            counters["transcript_deleted"] += 1
        else:
            counters["audio_deleted"] += 1
    if counters["audio_deleted"] or counters["transcript_deleted"]:
        logger.info("orphan_sweep_done", **counters)
    return counters
