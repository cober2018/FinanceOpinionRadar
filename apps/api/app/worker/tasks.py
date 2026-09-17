"""EPIC-02 Celery 任务：账号发现 + 到期派发（RAD-023）；EPIC-03：prepare 编排 + 存量补扫。
Plan #4 Task 2：adapter 按 platform 分流（douyin → 外部 dtk，其余 → yt-dlp 通用）。
"""

import random

import structlog

from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.services import discovery
from app.services.media.contracts import AdapterError, MediaSourceAdapter
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


def build_adapter(
    platform: str | None = None, *, discover_max_pages: int | None = None
) -> MediaSourceAdapter:
    # F1：工厂在 services/media，worker 不依赖 app.api；platform=douyin 走外部 dtk
    from app.services.media.factory import get_media_adapter

    return get_media_adapter(platform, discover_max_pages=discover_max_pages)


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
        platform = _account_platform(session, account_id)
        pages = None
        if platform == "douyin":  # 安全设置：DB 覆盖 env 的发现翻页上限（防风控节流）
            from app.services.live_status import get_security_settings

            pages = get_security_settings(session)["effective"]["douyin_discover_max_pages"]
        # 非 douyin 不传关键字，保持既有 build_adapter(platform) 调用形态（测试桩兼容）
        adapter = (
            build_adapter(platform, discover_max_pages=pages)
            if pages is not None
            else build_adapter(platform)
        )
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

    # 人类化错峰：同批到期账号在 0~N 秒内随机延迟派发，避免同一秒并发访问平台（0=关闭）
    session = get_session_factory()()
    try:
        from app.services.live_status import get_security_settings

        stagger_max = get_security_settings(session)["effective"][
            "discover_dispatch_stagger_max_sec"
        ]
    except Exception:  # noqa: BLE001 设置读取失败回落 env 默认，不阻断派发
        stagger_max = get_settings().discover_dispatch_stagger_max_sec
    try:
        due = SourceAccountRepository(session).list_due()
        for account in due:
            kwargs = {"countdown": random.uniform(0, stagger_max)} if stagger_max > 0 else {}
            celery_app.send_task("discover_source_account", args=[account.id], **kwargs)
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
    """注记②补扫：只接管"应自动转录"的 discovered 条目——账号开视频监控
    （discovery_mode=auto_poll）且非 backfill 回溯标记（首扫采标题不转写）。"""
    from app.db.models import SourceAccount
    from app.repositories.source_items import SourceItemRepository

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
    from app.services import live_ingest
    from app.services.transcription import get_transcription_provider

    session = get_session_factory()()
    try:
        return live_ingest.ingest_live_segments(
            session, get_transcription_provider()
        )
    finally:
        session.close()


@celery_app.task(name="prepare_live_segment")
def prepare_live_segment(item_id: int, segment_index: int, path: str) -> dict:
    from app.services import live_ingest

    return live_ingest.prepare_live_segment(item_id, segment_index, path)


@celery_app.task(name="dispatch_live_prepares")  # 失败恢复：缺号分片重派发
def dispatch_live_prepares() -> int:
    from app.services import live_ingest

    return live_ingest.dispatch_live_prepares()


# --- Plan #4 Task 6：值守桥 ---


@celery_app.task(name="sync_live_monitors")
def sync_live_monitors() -> dict:
    from app.repositories.source_accounts import SourceAccountRepository
    from app.services import recorder_bridge

    session = get_session_factory()()
    try:
        return recorder_bridge.sync_live_monitors(
            session, SourceAccountRepository(session)
        )
    finally:
        session.close()
