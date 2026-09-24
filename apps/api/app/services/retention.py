"""内容生命周期（Plan #6）：非精华条目到期物理删除，只留结论快照。

单轮 sweep（beat 每日 + 手动 API）对每个到期条目依次：
  1. 读全部观点 → 打包 summary_json（D3：Viewpoint 随 item CASCADE，必须先快照）
  2. 写 content_summary（无观点也留档：viewpoints=[]，标题/主播/日期在）
  3. 写 DeletedItemRef 墓碑（D4：否则 discover 下一轮重导同一视频，清理变死循环）
  4. 物理删 item（transcript/media/弹幕/观点全部级联）
  5. 删 jsonl 弹幕档案（<sink>/<日期>/<item_id>.jsonl，按文件名精确匹配）

范围（D1）：status IN (transcribed, failed, reviewing, ready) 且非精华且过期；
discovered/resolved/media_ready（小且可能待转写）、transcribing（直播进行中）、
extracting（抽取进行中）跳过。精华（is_asset）永不清。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.models import ContentSummary, DeletedItemRef, MediaAsset, SourceItem, Viewpoint

logger = structlog.get_logger(__name__)

# 单飞闸：sweep 是批删长事务，beat 重叠执行会造成重复快照/墓碑撞车
_SWEEP_LOCK_KEY = 861_205_302
# D1 清理范围
_SWEEPABLE_STATUSES = ("transcribed", "failed", "reviewing", "ready")


def _cutoff(retention_days: int, now: datetime) -> datetime:
    return now - timedelta(days=retention_days)


def _to_float(value: object) -> float | None:
    """Numeric 列经 ORM 返回 Decimal，JSON 序列化前归一。"""
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


def snapshot_viewpoints(session, item_id: int) -> list[dict]:
    """item 全部观点 → JSON 安全的字典列表（D3 契约）。"""
    rows = (
        session.query(
            Viewpoint.claim,
            Viewpoint.stance,
            Viewpoint.confidence,
            Viewpoint.importance,
            Viewpoint.as_of_date,
            Viewpoint.verification_status,
        )
        .filter(Viewpoint.source_item_id == item_id)
        .order_by(Viewpoint.id)
        .all()
    )
    return [
        {
            "claim": claim,
            "stance": stance,
            "confidence": _to_float(confidence),
            "importance": _to_float(importance),
            "as_of_date": as_of.isoformat() if as_of else None,
            "verification_status": verification_status,
        }
        for claim, stance, confidence, importance, as_of, verification_status in rows
    ]


def _delete_sink_file(sink_dir: str, item_id: int) -> bool:
    """删该条目的 jsonl 弹幕档案（文件名 = item_id，任意日期段）。"""
    if not sink_dir:
        return False
    root = Path(sink_dir)
    if not root.is_dir():
        return False
    removed = False
    for path in root.glob(f"*/{item_id}.jsonl"):
        try:
            path.unlink()
            removed = True
        except OSError as exc:
            logger.warning("retention_sink_unlink_failed", path=str(path), error=str(exc)[:120])
    return removed


def sweep_expired_content(session, *, dry_run: bool = False, settings=None) -> dict:
    """beat / 手动 API 入口。dry_run=true 只统计不删除（预览）。"""
    s = settings or get_settings()
    zero = {"expired": 0, "swept": 0, "snapshots": 0, "tombstones": 0, "sink_files": 0}
    retention_days = s.content_retention_days
    if not retention_days or retention_days <= 0:
        logger.info("retention_sweep_noop", reason="content_retention_days=0（禁用）")
        return {**zero, "noop": "content_retention_days=0"}

    locked = session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": _SWEEP_LOCK_KEY}
    ).scalar()
    session.commit()
    if not locked:
        logger.info("retention_sweep_skipped_singleton")
        return {**zero, "skipped": "singleton"}
    try:
        cutoff = _cutoff(retention_days, datetime.now(UTC))
        items = (
            session.query(SourceItem)
            .filter(
                SourceItem.status.in_(_SWEEPABLE_STATUSES),
                SourceItem.is_asset.is_(False),
                SourceItem.created_at < cutoff,
            )
            .order_by(SourceItem.id)
            .all()
        )
        # 取消精华的宽限期：判定基准 = max(created_at, unasset_at)。
        # 否则"标精华 40 天后取消"会立即过期——精华→普通切换的陷阱（2026-09-19 修复）。
        def _effective_created(item) -> datetime:
            unasset_at = (item.metadata_json or {}).get("unasset_at")
            if unasset_at:
                try:
                    return max(
                        item.created_at, datetime.fromisoformat(unasset_at)
                    )
                except (ValueError, TypeError):
                    return item.created_at
            return item.created_at

        items = [it for it in items if _effective_created(it) < cutoff]
        counters = {**zero, "expired": len(items)}
        if dry_run:
            logger.info("retention_sweep_dry_run", **counters)
            return counters

        creator_names: dict[int, str | None] = {}
        if items:
            from app.db.models import Creator, SourceAccount

            account_ids = {i.source_account_id for i in items}
            for acc_id, name in (
                session.query(SourceAccount.id, Creator.display_name)
                .join(Creator, Creator.id == SourceAccount.creator_id)
                .filter(SourceAccount.id.in_(account_ids))
                .all()
            ):
                creator_names[acc_id] = name

        for item in items:
            viewpoints = snapshot_viewpoints(session, item.id)
            # 转录保留引用（用户语义 2026-09-24）：自然过期条目的转录文件留存，
            # 登记 storage_uri 供孤儿回收豁免（手动删除的不登记——那类全删）
            transcript_refs = [
                uri
                for (uri,) in session.query(MediaAsset.storage_uri)
                .filter(
                    MediaAsset.source_item_id == item.id,
                    MediaAsset.asset_type == "transcript",
                )
                .all()
                if uri
            ]
            account = (
                session.get(SourceAccount, item.source_account_id)
                if item.source_account_id
                else None
            )
            session.add(
                ContentSummary(
                    platform=account.platform if account else "douyin",
                    creator_name=creator_names.get(item.source_account_id) or "未知来源",
                    item_title=item.title,
                    item_type=item.item_type,
                    item_created_at=item.created_at,
                    item_published_at=item.published_at,
                    viewpoints=viewpoints,
                    transcript_refs=transcript_refs,
                )
            )
            counters["snapshots"] += 1
            exists = (
                session.query(DeletedItemRef.id)
                .filter(
                    DeletedItemRef.source_account_id == item.source_account_id,
                    DeletedItemRef.external_item_id == item.external_item_id,
                )
                .first()
            )
            if exists is None:
                session.add(
                    DeletedItemRef(
                        source_account_id=item.source_account_id,
                        external_item_id=item.external_item_id,
                    )
                )
                counters["tombstones"] += 1
            if _delete_sink_file(s.danmaku_sink_dir, item.id):
                counters["sink_files"] += 1
            session.delete(item)
            counters["swept"] += 1
        session.commit()
        logger.info("retention_sweep_done", retention_days=retention_days, **counters)
        return counters
    finally:
        session.rollback()
        session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SWEEP_LOCK_KEY})
        session.commit()
