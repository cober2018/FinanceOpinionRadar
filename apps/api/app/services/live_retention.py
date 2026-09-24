"""直播原始分片保留策略（用户 2026-09-24 确认）：磁盘 .ts 按 mtime 超 N 天删除。

转写远快于保留期（5 分钟分片，ingest 10 分钟一轮），mtime 过期必然已转写或会话
已死；转录文本已入库、观点已产出，原始分片删除不影响任何成果数据。
删除后空目录（日期目录/主播目录）自底向上清理。
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from app.core.settings import get_settings

logger = structlog.get_logger(__name__)


def sweep_live_segments(settings=None, *, now: datetime | None = None) -> dict:
    """beat/手动入口：返回 {noop|files_deleted, dirs_deleted, bytes_freed}。"""
    s = settings or get_settings()
    if not s.live_segments_dir or s.live_segment_retention_days <= 0:
        logger.info("live_segment_retention_noop", reason="未启用（目录未配置或天数=0）")
        return {"noop": True}
    root = Path(s.live_segments_dir)
    if not root.is_dir():
        return {"noop": True}
    cutoff = (now or datetime.now(UTC)) - timedelta(days=s.live_segment_retention_days)
    files_deleted, dirs_deleted, bytes_freed = 0, 0, 0
    for path in root.rglob("*.ts"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff.timestamp():
            try:
                path.unlink()
                files_deleted += 1
                bytes_freed += stat.st_size
            except OSError as exc:
                logger.warning("live_segment_unlink_failed", path=str(path)[:150], error=str(exc)[:120])
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        p = Path(dirpath)
        if p == root:
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
                dirs_deleted += 1
        except OSError:
            pass
    if files_deleted or dirs_deleted:
        logger.info(
            "live_segment_retention_done",
            files=files_deleted,
            dirs=dirs_deleted,
            mb_freed=round(bytes_freed / 1e6, 1),
        )
    return {"files_deleted": files_deleted, "dirs_deleted": dirs_deleted, "bytes_freed": bytes_freed}
