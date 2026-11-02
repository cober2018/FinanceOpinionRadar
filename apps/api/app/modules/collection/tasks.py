"""Celery 任务定义（collection 来源/采集域）。

任务名与原 app.worker.tasks 完全一致（兼容基线 evidence/baseline-2 §2）：
旧队列消息以同名任务派发，新 worker 直接消费。业务体只调用本域/上游域入口。
"""

import random  # noqa: F401  （部分任务模块使用；未用由 lint 摘除）

import structlog

from app.bootstrap.celery import celery_app
from app.modules.collection import discovery
from app.modules.content.media.contracts import MediaSourceAdapter
from app.runtime.db.session import get_session_factory
from app.runtime.settings import get_settings

logger = structlog.get_logger(__name__)


def build_adapter(
    platform: str | None = None,
    *,
    discover_max_pages: int | None = None,
    proxy: str | None = None,
    proxy_key: str | None = None,
) -> MediaSourceAdapter:
    """媒体适配器工厂（引擎边界）：douyin 走本产品 engines/media，其余 yt-dlp。"""
    from app.modules.content.media.factory import get_media_adapter

    return get_media_adapter(
        platform,
        discover_max_pages=discover_max_pages,
        proxy=proxy,
        proxy_key=proxy_key,
    )


def _account_platform(session, account_id: int) -> str | None:
    account = session.get(discovery.SourceAccount, account_id)
    return account.platform if account else None


def _item_platform(session, item_id: int) -> str | None:
    from app.runtime.db.models import SourceAccount, SourceItem

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
            from app.modules.collection.live_status import get_security_settings

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
    from app.modules.sources.repository import SourceAccountRepository

    # 人类化错峰：同批到期账号在 0~N 秒内随机延迟派发，避免同一秒并发访问平台（0=关闭）
    session = get_session_factory()()
    try:
        from app.modules.collection.live_status import get_security_settings

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


# --- Plan #4 Task 6：值守桥（录制引擎生命周期归 collection）---


@celery_app.task(name="sync_live_monitors")
def sync_live_monitors() -> dict:
    from app.modules.collection.recorder_bridge import sync_live_monitors
    from app.modules.sources.repository import SourceAccountRepository

    session = get_session_factory()()
    try:
        return sync_live_monitors(
            session, SourceAccountRepository(session)
        )
    finally:
        session.close()


# --- Plan #5：直播弹幕采集（danmaku 队列）---


@celery_app.task(name="collect_danmaku")  # 单会话长任务（≤12h）；advisory lock 防重（F2）
def collect_danmaku(item_id: int, room_id: str) -> dict:
    from app.modules.collection.danmaku import collector

    return collector.collect_danmaku(item_id, room_id)


@celery_app.task(name="dispatch_danmaku_collectors")
def dispatch_danmaku_collectors() -> dict:
    from app.modules.collection.danmaku import dispatch

    session = get_session_factory()()
    try:
        return dispatch.dispatch_danmaku_collectors(session)
    finally:
        session.close()


@celery_app.task(name="ingest_danmaku_files")
def ingest_danmaku_files() -> dict:
    from app.modules.collection.danmaku import ingest

    session = get_session_factory()()
    try:
        return ingest.ingest_danmaku_files(session)
    finally:
        session.close()


@celery_app.task(name="qg_proxy_maintain")
def qg_proxy_maintain() -> dict:
    """青果长效代理自动维护（beat 每 10 分钟）：查询在用 → 对齐代理池；通道空则自动提取。

    全自动化目标（2026-09-21 与用户对齐）：除更换凭证外无需人工干预——
    24h 轮换到期、隧道出口变更，最迟一个周期内代理池自动跟上。
    查询失败（网络/凭证）不动池子，防止瞬时抖动误清出口。
    """
    from app.modules.collection import qg_proxy

    session = get_session_factory()()
    try:
        cfg = qg_proxy.get_qg_config(session)
        if not cfg.get("key") or not cfg.get("auth_pwd"):
            return {"skipped": "not_configured"}
        client = qg_proxy.get_qg_client(session)
        try:
            servers = [it["server"] for it in client.query() if it.get("server")]
        except qg_proxy.QgProxyError as exc:
            logger.warning("qg_maintain_query_failed", code=exc.code)
            return {"error": exc.code}
        pool = qg_proxy.sync_pool_into_settings(session, servers, cfg["key"], cfg["auth_pwd"])
        extracted = 0
        if not servers:
            # 通道空闲（到期/已释放）：尝试自动提取补位；权限不够等错误留给下轮
            try:
                ips = client.extract(num=1)
                new_servers = [it["server"] for it in ips if it.get("server")]
                pool = qg_proxy.sync_pool_into_settings(
                    session, new_servers, cfg["key"], cfg["auth_pwd"]
                )
                extracted = len(new_servers)
            except qg_proxy.QgProxyError as exc:
                logger.warning("qg_maintain_extract_failed", code=exc.code)
        logger.info(
            "qg_maintain_done",
            in_use=len(servers),
            extracted=extracted,
            pool_size=len(pool),
        )
        return {"in_use": len(servers), "extracted": extracted, "pool_size": len(pool)}
    finally:
        session.close()
