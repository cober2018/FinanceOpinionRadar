"""弹幕 jsonl → live_chat_message 入库（Plan #5 Task 4）。

幂等：全局单飞闸（pg advisory lock）+ 批内查重 + 唯一约束兜底（F5）。
半行/坏行/不支持类型跳过计数不中断（采集器 flush 粒度容错）。
每轮输出计数行供运维 grep（沿 F12 模式）。
"""

import json
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.settings import get_settings
from app.repositories.live_chat_messages import LiveChatMessageRepository
from app.services.danmaku.parse import loads_line, parse_business

logger = structlog.get_logger(__name__)

# pg advisory lock（radar danmaku ingest 专用，区别于 live ingest 的 861205300）
_INGEST_LOCK_KEY = 861_205_301
_BATCH_SIZE = 500
_DATE_DIR_LEN = 10  # YYYY-MM-DD


def parse_envelope_line(line: str):
    """jsonl 档案行 → DanmakuRecord；坏行/半行/不支持类型 → None（仅计数）。"""
    line = line.strip()
    if not line:
        return None
    try:
        envelope = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    doc = envelope.get("msg")
    if doc is None and isinstance(envelope.get("raw"), str):
        doc = loads_line(envelope["raw"])  # 采集期坏 JSON 的原文补偿解析
    if not isinstance(doc, dict):
        return None
    return parse_business(doc)


def ingest_danmaku_files(session, *, repo=None, settings=None) -> dict:
    """beat 入口：扫 sink 目录 → 按文件批量入库。返回计数行。"""
    s = settings or get_settings()
    zero = {"files": 0, "files_skipped": 0, "messages_inserted": 0, "messages_skipped": 0}
    if not s.danmaku_sink_dir:
        logger.info("danmaku_ingest_noop", reason="danmaku_sink_dir 未配置")
        return {**zero, "noop": "danmaku_sink_dir 未配置"}
    root = Path(s.danmaku_sink_dir)
    if not root.is_dir():
        logger.warning("danmaku_ingest_dir_missing", path=str(root))
        return zero

    locked = session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": _INGEST_LOCK_KEY}
    ).scalar()
    session.commit()
    if not locked:
        logger.info("danmaku_ingest_skipped_singleton")
        return {**zero, "skipped": "singleton"}
    try:
        return _ingest_all(session, root, repo)
    finally:
        session.rollback()
        session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _INGEST_LOCK_KEY})
        session.commit()


def _ingest_all(session, root: Path, repo) -> dict:
    counters = {"files": 0, "files_skipped": 0, "messages_inserted": 0, "messages_skipped": 0}
    repo = repo or LiveChatMessageRepository(session)
    for path in _iter_sink_files(root):
        item_id = int(path.stem)
        exists = session.execute(
            text("SELECT 1 FROM source_item WHERE id = :i"), {"i": item_id}
        ).first()
        if exists is None:
            counters["files_skipped"] += 1
            logger.warning("danmaku_ingest_no_item", path=str(path))
            continue
        counters["files"] += 1
        batch: list = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                record = parse_envelope_line(line)
                if record is None:
                    counters["messages_skipped"] += 1
                    continue
                batch.append(record)
                if len(batch) >= _BATCH_SIZE:
                    counters["messages_inserted"] += repo.insert_many(item_id, batch)
                    batch.clear()
        if batch:
            counters["messages_inserted"] += repo.insert_many(item_id, batch)
        session.commit()
    logger.info("danmaku_ingest_scan", **counters)
    return counters


def _iter_sink_files(root: Path):
    """<root>/<YYYY-MM-DD>/<item_id>.jsonl 两层布局；其余一律忽略（文件名不进下游，F7）。"""
    for day_dir in sorted(root.iterdir()):
        if not (day_dir.is_dir() and len(day_dir.name) == _DATE_DIR_LEN):
            continue
        for path in sorted(day_dir.glob("*.jsonl")):
            if path.stem.isdigit():
                yield path
