"""来源发现编排（RAD-022/023）：resolve → 建/联账号 → upsert 条目 → 派生下游任务。"""

from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from app.db.models import SourceAccount, SourceItem
from app.repositories.creators import CreatorRepository
from app.repositories.source_accounts import SourceAccountRepository
from app.repositories.source_items import SourceItemRepository
from app.services.media.adapters.yt_dlp import normalize_channel_url
from app.services.media.contracts import (
    AccountRef,
    MediaSourceAdapter,
    ResolvedMedia,
)

logger = structlog.get_logger(__name__)

# send 的具体实现由 worker 装配传入；服务层只依赖 Callable，便于测试（C7）
SendFunc = Callable[..., object]

FALLBACK_CREATOR_NAME = "未知来源"


def resolve_url_preview(url: str, session: Session, adapter: MediaSourceAdapter) -> ResolvedMedia:
    """RAD-022 预览：只解析不落库。"""
    return adapter.resolve(url)


def get_or_create_creator(session: Session, display_name: str | None):
    """creator get-or-create（C5）：按名幂等，空名回退占位（Plan #4 Task 3 账号 API 复用）。"""
    creators = CreatorRepository(session)
    creator = creators.get_by_name(display_name or FALLBACK_CREATOR_NAME)
    if creator is None:
        creator = creators.create(display_name=display_name or FALLBACK_CREATOR_NAME)
    return creator


def create_item_from_url(url: str, session: Session, adapter: MediaSourceAdapter) -> SourceItem:
    """RAD-022 确认后创建：服务端重新 resolve（C4），账号/creator get-or-create（C5）。"""
    media = adapter.resolve(url)
    creator = get_or_create_creator(session, media.channel_name)

    accounts = SourceAccountRepository(session)
    # channel 级账号；无 channel 信息回退为"每视频一账号"
    account_external_id = media.channel_external_id or media.external_item_id
    account = accounts.upsert_by_external(
        creator_id=creator.id,
        platform=media.platform,
        external_id=account_external_id,
        url=normalize_channel_url(media.channel_url, platform=media.platform),  # 注记③
        discovery_mode="manual",
    )
    item, _created = SourceItemRepository(session).upsert_by_external(
        source_account_id=account.id,
        external_item_id=media.external_item_id,
        title=media.title,
        canonical_url=media.canonical_url,
        thumbnail_url=media.thumbnail_url,
        published_at=media.published_at,
        duration_ms=media.duration_ms,
        item_type=media.item_type,
        metadata_json={
            "resolved": True,
            **{t.language: t.is_auto for t in media.subtitles},
        },
    )
    session.commit()
    logger.info(
        "source_item_upserted",
        item_id=item.id,
        account_id=account.id,
        external_item_id=item.external_item_id,
        created=_created,
    )
    return item


def discover_account(
    account_id: int,
    session: Session,
    adapter: MediaSourceAdapter,
    *,
    send: SendFunc,
) -> dict:
    """RAD-023：load → discover → upsert → 新条目派发 → 记成功；失败计数并重抛（F2）。"""
    account = session.get(SourceAccount, account_id)
    if account is None or not account.enabled:
        return {"account_id": account_id, "skipped": True, "discovered": 0, "created": 0}

    # rollback 后 account 属性会过期，先取日志要用的字段
    platform = account.platform
    try:
        items = adapter.discover(
            AccountRef(
                platform=account.platform,
                external_id=account.external_id,
                url=account.url,
                config=account.config_json,
            )
        )
        repo = SourceItemRepository(session)
        created_ids: list[int] = []
        for discovered in items:
            item, is_new = repo.upsert_by_external(
                source_account_id=account.id,
                external_item_id=discovered.external_item_id,
                title=discovered.title,
                canonical_url=discovered.url,
                duration_ms=discovered.duration_ms,
                metadata_json={"discovered_via": "discover_job"},
            )
            if is_new:
                created_ids.append(item.id)
        account.last_success_at = datetime.now(UTC)
        account.failure_count = 0
        session.commit()
    except Exception:
        # F2：任务边界捕获——记完整上下文后重抛，绝不吞错
        session.rollback()
        fresh = session.get(SourceAccount, account_id)
        if fresh is not None:
            fresh.failure_count += 1
            session.commit()
        logger.exception(
            "discover_failed",
            account_id=account_id,
            platform=platform,
        )
        raise

    for item_id in created_ids:
        # C7：按名投递，EPIC-03 落地实现；G1：commit 后 send 失败由 EPIC-03 存量补扫兜底
        send("prepare_source_item", args=[item_id])
    logger.info(
        "discover_ok", account_id=account_id, discovered=len(items), created=len(created_ids)
    )
    return {
        "account_id": account_id,
        "skipped": False,
        "discovered": len(items),
        "created": len(created_ids),
    }
