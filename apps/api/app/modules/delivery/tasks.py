"""Celery 任务定义（delivery 交付/推送域）。

任务名与原 app.worker.tasks 完全一致（兼容基线 evidence/baseline-2 §2）：
旧队列消息以同名任务派发，新 worker 直接消费。业务体只调用本域/上游域入口。
"""

import random  # noqa: F401  （部分任务模块使用；未用由 lint 摘除）

import structlog

from app.bootstrap.celery import celery_app
from app.runtime.db.session import get_session_factory

logger = structlog.get_logger(__name__)

@celery_app.task(name="push_confirmed_viewpoints")
def push_confirmed_viewpoints() -> dict:
    """Plan #7：确认观点推送扫描——未投递的 confirmed 观点按渠道投出。"""
    from app.modules.delivery import push

    session = get_session_factory()()
    try:
        return push.deliver_pending_pushes(session)
    finally:
        session.close()


@celery_app.task(name="push_reviewed_video_summaries")
def push_reviewed_video_summaries() -> dict:
    from app.modules.delivery.video_summary_push import deliver_pending_summary_events

    session = get_session_factory()()
    try:
        return deliver_pending_summary_events(session)
    finally:
        session.close()
