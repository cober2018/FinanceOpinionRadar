"""EPIC-02 Celery 任务：账号发现 + 到期派发（RAD-023）；EPIC-03：prepare 编排 + 存量补扫。
Plan #4 Task 2：adapter 按 platform 分流（douyin → 外部 dtk，其余 → yt-dlp 通用）。
"""

import structlog

from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.services import discovery
from app.services.media.contracts import AdapterError, MediaSourceAdapter
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


def build_adapter(platform: str | None = None) -> MediaSourceAdapter:
    # F1：工厂在 services/media，worker 不依赖 app.api；platform=douyin 走外部 dtk
    from app.services.media.factory import get_media_adapter

    return get_media_adapter(platform)


def _account_platform(session, account_id: int) -> str | None:
    account = session.get(discovery.SourceAccount, account_id)
    return account.platform if account else None


def _item_platform(session, item_id: int) -> str | None:
    from app.db.models import SourceAccount, SourceItem

    item = session.get(SourceItem, item_id)
    if item is None:
        return None
    account = session.get(SourceAccount, item.source_account_id)
    return account.platform if account else None


@celery_app.task(name="discover_source_account")  # F5：无自动重试——失败计数在编排层，重试会双计
def discover_source_account(account_id: int) -> dict:
    session = get_session_factory()()
    try:
        adapter = build_adapter(_account_platform(session, account_id))
        return discovery.discover_account(
            account_id, session, adapter, send=celery_app.send_task
        )
    finally:
        session.close()


@celery_app.task(name="dispatch_due_discoveries")
def dispatch_due_discoveries() -> int:
    """C6：扫描 enabled 且到期（last_success_at + poll_interval_sec < now）的账号并派发。

    到期判定下沉在 SourceAccountRepository.list_due（E5），任务只做查询与派发。
    """
    from app.repositories.source_accounts import SourceAccountRepository

    session = get_session_factory()()
    try:
        due = SourceAccountRepository(session).list_due()
        for account in due:
            celery_app.send_task("discover_source_account", args=[account.id])
        logger.info("dispatch_due_discoveries", dispatched=len(due))
        return len(due)
    finally:
        session.close()


@celery_app.task(name="prepare_source_item")  # 注记①：幂等在编排层（行锁+状态门槛），重复投递安全
def prepare_source_item(item_id: int) -> dict:
    from app.services.preparation import prepare_source_item as run_prepare
    from app.services.storage import get_storage
    from app.services.transcription import get_transcription_provider

    session = get_session_factory()()
    try:
        try:
            adapter = build_adapter(_item_platform(session, item_id))
        except AdapterError as exc:
            # douyin 未配置等构造期失败：与其他 prepare 失败同语义落 last_error（F8）
            from app.services.preparation import record_stage_failure

            return record_stage_failure(session, item_id, "resolve", exc)
        return run_prepare(
            item_id,
            session,
            adapter,
            get_storage(),
            get_transcription_provider(),
        )
    finally:
        session.close()


@celery_app.task(name="dispatch_pending_prepares")  # 注记②：周期补扫 discovered（G1 兜底）
def dispatch_pending_prepares() -> int:
    from app.repositories.source_items import SourceItemRepository

    session = get_session_factory()()
    try:
        pending = SourceItemRepository(session).list_by_status(
            "discovered", limit=get_settings().prepare_sweep_batch_size
        )
        for item in pending:
            celery_app.send_task("prepare_source_item", args=[item.id])
        logger.info("dispatch_pending_prepares", dispatched=len(pending))
        return len(pending)
    finally:
        session.close()
