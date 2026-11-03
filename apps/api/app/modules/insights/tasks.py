"""Celery 任务定义（insights 观点/审核/总结域）。

任务名与原 app.worker.tasks 完全一致（兼容基线 evidence/baseline-2 §2）：
旧队列消息以同名任务派发，新 worker 直接消费。业务体只调用本域/上游域入口。
"""

import random  # noqa: F401  （部分任务模块使用；未用由 lint 摘除）

import structlog

from app.bootstrap.celery import celery_app
from app.runtime.db.session import get_session_factory
from app.runtime.settings import get_settings

logger = structlog.get_logger(__name__)

@celery_app.task(name="extract_source_item_viewpoints")
def extract_source_item_viewpoints(item_id: int) -> dict:
    from datetime import UTC, datetime

    from app.modules.insights.extraction import extract_source_item
    from app.runtime.db.models import JobRun, SourceItem

    session = get_session_factory()()
    # 条目已被物理删除（重投/迟到消息）：JobRun 有 FK，先防一手免得刷 FK 错
    if session.get(SourceItem, item_id) is None:
        session.close()
        return {"item_id": item_id, "status": "skipped_no_item"}
    run = JobRun(job_type="extract_viewpoints", source_item_id=item_id, status="running")
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    def _finish(status: str, error: str | None = None, payload: dict | None = None) -> None:
        # 独立会话写终态：主会话可能已被异常污染（PendingRollback）
        s2 = get_session_factory()()
        try:
            r = s2.get(JobRun, run_id)
            if r is not None:
                r.status = status
                r.finished_at = datetime.now(UTC)
                if error:
                    r.error_code = "EXTRACT_FAILED"
                    r.error_message = error[:500]
                if payload:
                    r.payload_json = payload
                s2.commit()
        finally:
            s2.close()

    session = get_session_factory()()
    try:
        out = extract_source_item(session, item_id)
        _finish("success", payload={"created": out.get("created"), "run_uri": out.get("run_uri")})
        return out
    except Exception as exc:
        _finish("failed", error=str(exc))
        raise
    finally:
        session.close()


@celery_app.task(name="dispatch_pending_extractions")
def dispatch_pending_extractions() -> int:
    """EPIC-04 补扫：transcribed 且应自动抽取（非 backfill）的条目派发。

    vod 要求账号 auto_poll（手动模式账号的旧视频由用户手动抽取）；live 不限——
    纯直播值守账号（manual）的直播转录完也要自动抽取（用户 2026-09-28）。
    """
    from sqlalchemy import or_

    from app.runtime.db.models import SourceAccount, SourceItem

    session = get_session_factory()()
    try:
        pending = (
            session.query(SourceItem)
            .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
            .filter(
                SourceItem.status == "transcribed",
                SourceItem.item_type.in_(["vod", "live"]),
                SourceAccount.enabled.is_(True),
                or_(
                    SourceAccount.discovery_mode == "auto_poll",
                    SourceItem.item_type == "live",
                ),
            )
            .order_by(SourceItem.id)
            .limit(get_settings().prepare_sweep_batch_size)
            .all()
        )
        dispatched = 0
        for item in pending:
            if (item.metadata_json or {}).get("backfill"):
                continue
            celery_app.send_task("extract_source_item_viewpoints", args=[item.id])
            dispatched += 1
        # 兜底：reviewing 但无待审观点的条目收尾 ready + 派总结（自动确认路径
        # 断链、任务中断等一切漏网；2026-09-28 直播实录 931/966 永卡 reviewing）
        from app.modules.insights.models import Viewpoint
        from app.modules.insights.summarizer import promote_item_if_reviewed

        promoted = 0
        for item in (
            session.query(SourceItem)
            .filter(
                SourceItem.status == "reviewing",
                ~session.query(Viewpoint.id)
                .where(
                    Viewpoint.source_item_id == SourceItem.id,
                    Viewpoint.verification_status.in_(("candidate", "needs_review")),
                )
                .exists(),
            )
            .limit(get_settings().prepare_sweep_batch_size)
            .all()
        ):
            if promote_item_if_reviewed(session, item):
                promoted += 1
        # 兜底：reviewing 且仍有待审 → 派 LLM 二道复核（EPIC-05）。metadata 记
        # 请求时间戳，1 小时内不重派——LLM 任务失败/丢消息时按此节奏重试
        from datetime import UTC, datetime

        from app.modules.insights.reviewer import dispatch_llm_review, llm_review_enabled

        llm_dispatched = 0
        if llm_review_enabled(session):
            for item in (
                session.query(SourceItem)
                .filter(
                    SourceItem.status == "reviewing",
                    session.query(Viewpoint.id)
                    .where(
                        Viewpoint.source_item_id == SourceItem.id,
                        Viewpoint.verification_status == "needs_review",
                    )
                    .exists(),
                )
                .limit(get_settings().prepare_sweep_batch_size)
                .all()
            ):
                meta = dict(item.metadata_json or {})
                last = meta.get("llm_review_requested_at")
                if last:
                    try:
                        if (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() < 3600:
                            continue
                    except ValueError:
                        pass
                meta["llm_review_requested_at"] = datetime.now(UTC).isoformat()
                item.metadata_json = meta
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(item, "metadata_json")
                session.commit()
                dispatch_llm_review(item.id)
                llm_dispatched += 1
        logger.info(
            "dispatch_pending_extractions",
            dispatched=dispatched,
            promoted=promoted,
            llm_review_dispatched=llm_dispatched,
        )
        return dispatched
    finally:
        session.close()


@celery_app.task(name="llm_review_source_item")
def llm_review_source_item(item_id: int) -> dict:
    """EPIC-05：LLM 二道复核——规则复核未放行的候选逐条裁决（只放行不驳回）。"""
    from app.modules.insights.reviewer import llm_review_pending
    from app.runtime.db.models import JobRun

    session = get_session_factory()()
    job_id = None
    try:
        job = JobRun(job_type="llm_review", source_item_id=item_id, status="running")
        session.add(job)
        session.commit()
        job_id = job.id
        out = llm_review_pending(session, item_id)
        with get_session_factory()() as s2:
            row = s2.get(JobRun, job_id)
            row.status = "success"
            row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
            row.payload_json = {"result": out}
            s2.commit()
        return out
    except Exception as exc:
        if job_id:
            with get_session_factory()() as s2:
                row = s2.get(JobRun, job_id)
                row.status = "failed"
                row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
                row.error_message = str(exc)[:500]
                s2.commit()
        raise
    finally:
        session.close()


@celery_app.task(name="extract_live_incremental")
def extract_live_incremental(item_id: int) -> dict:
    """直播滚动总结（2026-09-30）：分片转写后的增量观点抽取（水位幂等）。"""
    from app.modules.collection.live_rolling import extract_live_incremental
    from app.runtime.db.models import JobRun, SourceItem

    session = get_session_factory()()
    job_id = None
    if session.get(SourceItem, item_id) is None:
        session.close()
        return {"item_id": item_id, "status": "skipped_no_item"}
    try:
        job = JobRun(job_type="live_incremental_extract", source_item_id=item_id, status="running")
        session.add(job)
        session.commit()
        job_id = job.id
        out = extract_live_incremental(session, item_id)
        with get_session_factory()() as s2:
            row = s2.get(JobRun, job_id)
            row.status = "success"
            row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
            row.payload_json = {"result": out}
            s2.commit()
        return out
    except Exception as exc:
        if job_id:
            with get_session_factory()() as s2:
                row = s2.get(JobRun, job_id)
                row.status = "failed"
                row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
                row.error_message = str(exc)[:500]
                s2.commit()
        raise
    finally:
        session.close()


@celery_app.task(name="rolling_summarize_live")
def rolling_summarize_live(item_id: int) -> dict:
    """直播滚动总结：confirmed 观点全集重算 viewpoint_summary（直播中滚动覆盖）。"""
    from app.modules.collection.live_rolling import rolling_summarize_live
    from app.runtime.db.models import JobRun, SourceItem

    session = get_session_factory()()
    job_id = None
    if session.get(SourceItem, item_id) is None:
        session.close()
        return {"item_id": item_id, "status": "skipped_no_item"}
    try:
        job = JobRun(job_type="rolling_summarize", source_item_id=item_id, status="running")
        session.add(job)
        session.commit()
        job_id = job.id
        out = rolling_summarize_live(session, item_id)
        with get_session_factory()() as s2:
            row = s2.get(JobRun, job_id)
            row.status = "success"
            row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
            row.payload_json = {"result": out}
            s2.commit()
        return out
    except Exception as exc:
        if job_id:
            with get_session_factory()() as s2:
                row = s2.get(JobRun, job_id)
                row.status = "failed"
                row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
                row.error_message = str(exc)[:500]
                s2.commit()
        raise
    finally:
        session.close()


@celery_app.task(name="live_finalize_after_close")
def live_finalize_after_close(item_id: int) -> dict:
    """已增量抽取的直播 close 收尾：推进状态机 + 统一收尾闸（不全文重抽）。"""
    from app.modules.collection.live_rolling import finalize_after_close
    from app.runtime.db.models import JobRun, SourceItem

    session = get_session_factory()()
    job_id = None
    if session.get(SourceItem, item_id) is None:
        session.close()
        return {"item_id": item_id, "status": "skipped_no_item"}
    try:
        job = JobRun(job_type="live_finalize", source_item_id=item_id, status="running")
        session.add(job)
        session.commit()
        job_id = job.id
        out = finalize_after_close(session, item_id)
        with get_session_factory()() as s2:
            row = s2.get(JobRun, job_id)
            row.status = "success"
            row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
            row.payload_json = {"result": out}
            s2.commit()
        return out
    except Exception as exc:
        if job_id:
            with get_session_factory()() as s2:
                row = s2.get(JobRun, job_id)
                row.status = "failed"
                row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
                row.error_message = str(exc)[:500]
                s2.commit()
        raise
    finally:
        session.close()


@celery_app.task(name="summarize_source_item_viewpoints")
def summarize_source_item_viewpoints(item_id: int) -> dict:
    """视频观点一句话总结（最后一个观点复核完自动 / 抽屉手动重生成）。"""
    from app.runtime.db.models import JobRun, SourceItem

    session = get_session_factory()()
    job_id = None
    if session.get(SourceItem, item_id) is None:  # 迟到/重投消息的 FK 防护
        session.close()
        return {"item_id": item_id, "status": "skipped_no_item"}
    try:
        job = JobRun(job_type="summarize_viewpoints", source_item_id=item_id, status="running")
        session.add(job)
        session.commit()
        job_id = job.id
        from app.modules.workflows import summarize_item_viewpoints

        out = summarize_item_viewpoints(session, item_id)
        with get_session_factory()() as s2:
            row = s2.get(JobRun, job_id)
            row.status = "success"
            row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
            row.payload_json = {"result": out}
            s2.commit()
        return out
    except Exception as exc:
        if job_id:
            with get_session_factory()() as s2:
                row = s2.get(JobRun, job_id)
                row.status = "failed"
                row.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
                row.error_message = str(exc)[:500]
                s2.commit()
        raise
    finally:
        session.close()
