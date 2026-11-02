"""Celery 任务定义（runtime 基础设施）。"""

import structlog

from app.bootstrap.celery import celery_app
from app.runtime.db.session import get_session_factory
from app.runtime.settings import get_settings

logger = structlog.get_logger(__name__)


@celery_app.task(name="housekeeping_stale_jobs")
def housekeeping_stale_jobs() -> dict:
    """运维 housekeeping：
    ① 僵尸清理：running 超 2 小时的任务标记 failed（worker 中断等场景的收尾）；
    ② 运维流水保留：job_run / api_call_log 超 ops_retention_days（默认 30 天）物理删除
      ——两张表只写不删会无限增长（2026-10-12 盘点：job_run 已 1.1 万行）。
    注意：2026-10-12 前该任务未接 beat，从未自动执行过。
    """
    from datetime import UTC, datetime, timedelta

    from app.runtime.db.models import ApiCallLog, JobRun

    session = get_session_factory()()
    try:
        cutoff = datetime.now(UTC) - timedelta(hours=2)
        stale = (
            session.query(JobRun)
            .filter(JobRun.status == "running", JobRun.created_at < cutoff)
            .all()
        )
        for r in stale:
            r.status = "failed"
            r.error_code = "STALE_TIMEOUT"
            r.error_message = "任务超 2 小时未结束（worker 中断），由 housekeeping 收尾"
            r.finished_at = datetime.now(UTC)
        session.commit()
        if stale:
            logger.info("housekeeping_stale_jobs", marked=len(stale))

        out = {"stale_marked": len(stale), "job_run_pruned": 0, "api_call_log_pruned": 0}
        days = get_settings().ops_retention_days
        if days > 0:
            row_cutoff = datetime.now(UTC) - timedelta(days=days)
            out["job_run_pruned"] = (
                session.query(JobRun)
                .filter(JobRun.created_at < row_cutoff)
                .delete(synchronize_session=False)
            )
            out["api_call_log_pruned"] = (
                session.query(ApiCallLog)
                .filter(ApiCallLog.created_at < row_cutoff)
                .delete(synchronize_session=False)
            )
            session.commit()
            if out["job_run_pruned"] or out["api_call_log_pruned"]:
                logger.info("housekeeping_ops_rows_pruned", **out)
        return out
    finally:
        session.close()
