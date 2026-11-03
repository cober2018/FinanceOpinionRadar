"""Celery 任务定义（content 素材/转写域）。

任务名与原 app.worker.tasks 完全一致（兼容基线 evidence/baseline-2 §2）：
旧队列消息以同名任务派发，新 worker 直接消费。业务体只调用本域/上游域入口。
"""

import random  # noqa: F401  （部分任务模块使用；未用由 lint 摘除）

import structlog

from app.bootstrap.celery import celery_app
from app.modules.collection.tasks import (
    build_adapter,  # 引擎工厂单点（collection→content 允许方向）
)
from app.modules.content.media.contracts import AdapterError
from app.runtime.db.session import get_session_factory
from app.runtime.settings import get_settings

logger = structlog.get_logger(__name__)


@celery_app.task(name="prepare_source_item")  # 注记①：幂等在编排层（行锁+状态门槛），重复投递安全
def prepare_source_item(item_id: int) -> dict:
    from datetime import UTC, datetime

    from app.modules.collection.proxy_pool import get_proxy_pool
    from app.modules.content.preparation import prepare_source_item as run_prepare
    from app.modules.content.transcription import get_transcription_provider
    from app.runtime.db.models import JobRun, SourceItem
    from app.runtime.storage import get_storage

    factory = get_session_factory()
    s = factory()
    run = JobRun(job_type="prepare_media", source_item_id=item_id, status="running")
    s.add(run)
    s.commit()
    run_id = run.id
    s.close()

    def _finish(status: str, error: str | None = None, payload: dict | None = None) -> None:
        s2 = factory()
        try:
            r = s2.get(JobRun, run_id)
            if r is not None:
                r.status = status
                r.finished_at = datetime.now(UTC)
                if error:
                    r.error_code = "PREPARE_FAILED"
                    r.error_message = error[:500]
                if payload:
                    r.payload_json = payload
                s2.commit()
        finally:
            s2.close()

    session = factory()
    try:
        try:
            item_row = session.get(SourceItem, item_id)
            account_id = item_row.source_account_id if item_row else None
            # 代理池稳定绑定：同账号恒走同一出口（设置页「安全 → 代理池」配置）
            proxy = get_proxy_pool().pick(session, str(account_id or item_id))
            adapter = build_adapter(
                _item_platform(session, item_id),
                proxy=proxy,
                proxy_key=str(account_id or item_id),
            )
        except AdapterError as exc:
            # douyin 未配置等构造期失败：与其他 prepare 失败同语义落 last_error（F8）
            from app.modules.content.preparation import record_stage_failure

            out = record_stage_failure(session, item_id, "resolve", exc)
            _finish("failed", error=str(exc))
            return out
        out = run_prepare(
            item_id,
            session,
            adapter,
            get_storage(),
            get_transcription_provider(),
        )
        _finish(
            "success",
            payload={"status": out.get("status"), "segments": out.get("segments")},
        )
        return out
    except Exception as exc:
        _finish("failed", error=str(exc))
        raise
    finally:
        session.close()


def _item_platform(session, item_id: int) -> str | None:
    from app.modules.collection.tasks import _item_platform as impl

    return impl(session, item_id)


@celery_app.task(name="dispatch_pending_prepares")  # 注记②：周期补扫 discovered（G1 兜底）
def dispatch_pending_prepares() -> int:
    """注记②补扫：只接管"应自动转录"的 discovered 条目——账号开视频监控
    （discovery_mode=auto_poll）且非 backfill 回溯标记（首扫采标题不转写）。"""
    from app.modules.content.repositories.source_items import SourceItemRepository
    from app.runtime.db.models import SourceAccount

    session = get_session_factory()()
    try:
        pending = SourceItemRepository(session).list_by_status(
            "discovered", limit=get_settings().prepare_sweep_batch_size
        )
        account_ids = {i.source_account_id for i in pending}
        accounts = {
            a.id: a
            for a in session.query(SourceAccount).filter(SourceAccount.id.in_(account_ids or [0]))
        }
        dispatched = 0
        for item in pending:
            account = accounts.get(item.source_account_id)
            if account is None or account.discovery_mode != "auto_poll":
                continue
            if (item.metadata_json or {}).get("backfill"):
                continue
            celery_app.send_task("prepare_source_item", args=[item.id])
            dispatched += 1
        logger.info("dispatch_pending_prepares", dispatched=dispatched)
        return dispatched
    finally:
        session.close()


# --- Plan #4 Task 5：直播分片 ingest ---


@celery_app.task(name="ingest_live_segments")  # E2：单飞闸在编排内（pg advisory lock）
def ingest_live_segments() -> dict:
    from app.modules import workflows
    from app.modules.content import live_ingest
    from app.modules.content.transcription import get_transcription_provider

    session = get_session_factory()()
    try:
        # 撤回协调入口经参数注入（content 域不反向依赖 workflows）：
        # 会话标题/起播时刻变化时，同事务撤回旧 ready 事件（design 2.2）
        return live_ingest.ingest_live_segments(
            session, get_transcription_provider(),
            withdraw=workflows.withdraw_if_invalid,
        )
    finally:
        session.close()


@celery_app.task(name="prepare_live_segment")
def prepare_live_segment(item_id: int, segment_index: int, path: str) -> dict:
    from app.modules.content import live_ingest

    return live_ingest.prepare_live_segment(item_id, segment_index, path)


@celery_app.task(name="dispatch_live_prepares")  # 失败恢复：缺号分片重派发
def dispatch_live_prepares() -> int:
    from app.modules.content import live_ingest

    return live_ingest.dispatch_live_prepares()


@celery_app.task(name="retry_failed_prepares")
def retry_failed_prepares() -> int:
    """失败转写自动重试（退避）：CDN 限速等运营性失败随时间自愈。

    间隔按尝试次数递增（30min → 1h → 2h → 4h → 8h），上限 5 次；
    记录在 metadata_json.retry = {count, last_at}，人工「转写」按钮重置计数。
    """
    from datetime import UTC, datetime, timedelta

    from app.runtime.db.models import SourceItem

    spacing = [timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2), timedelta(hours=4)]
    session = get_session_factory()()
    try:
        failed = (
            session.query(SourceItem)
            .filter(SourceItem.status == "failed", SourceItem.item_type == "vod")
            .order_by(SourceItem.id)
            .all()
        )
        now = datetime.now(UTC)
        dispatched = 0
        for item in failed:
            meta = item.metadata_json or {}
            # 会员专属等内容性永久失败：重试永远无意义，不占退避序列
            err = meta.get("last_error") or {}
            if meta.get("members_only") or err.get("code") == "MEMBERS_ONLY":
                continue
            retry = dict(meta.get("retry") or {})
            count = int(retry.get("count") or 0)
            if count >= 5:
                continue
            last = datetime.fromisoformat(retry["last_at"]) if retry.get("last_at") else None
            wait = spacing[min(count, len(spacing) - 1)]
            if last and now - last < wait:
                continue
            meta["retry"] = {"count": count + 1, "last_at": now.isoformat()}
            item.metadata_json = meta
            celery_app.send_task("prepare_source_item", args=[item.id])
            dispatched += 1
        session.commit()
        logger.info("retry_failed_prepares", dispatched=dispatched, failed_total=len(failed))
        return dispatched
    finally:
        session.close()


# --- Plan #6：内容生命周期 ---


@celery_app.task(name="sweep_content_retention")  # 单飞闸在编排内（pg advisory lock）
def sweep_content_retention() -> dict:
    from app.modules import workflows

    session = get_session_factory()()
    try:
        return workflows.sweep_expired_content(session)
    finally:
        session.close()


@celery_app.task(name="sweep_orphan_media_task")
def sweep_orphan_media_task() -> dict:
    """MinIO 孤儿对象每日回收（仅 audio/ 前缀；transcripts 显式触发才清）。"""
    from app.modules.content.orphan_sweep import sweep_orphan_media

    session = get_session_factory()()
    try:
        return sweep_orphan_media(session)
    finally:
        session.close()


@celery_app.task(name="sweep_live_segments_task")
def sweep_live_segments_task() -> dict:
    """直播原始分片 mtime 保留期清理（默认 7 天，0=禁用）。"""
    from app.modules.content.live_retention import sweep_live_segments

    return sweep_live_segments()


@celery_app.task(name="sweep_danmaku_sinks_task")
def sweep_danmaku_sinks_task() -> dict:
    """弹幕 jsonl 档案保留期清理（默认 7 天，0=禁用）。"""
    from app.modules.content.live_retention import sweep_danmaku_sinks

    return sweep_danmaku_sinks()
