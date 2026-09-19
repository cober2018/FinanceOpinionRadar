from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, HttpUrl
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import discovery
from app.services.media.contracts import (
    AdapterError,
    AdapterTimeoutError,
    MediaSourceAdapter,
    NotSingleItemError,
    UrlNotAllowedError,
)
from app.services.media.factory import detect_platform, get_media_adapter

router = APIRouter(prefix="/source-items", tags=["source-items"])


class ResolveUrlRequest(BaseModel):
    url: HttpUrl


class ResolveUrlResponse(BaseModel):
    platform: str
    external_id: str
    title: str | None
    duration_ms: int | None
    thumbnail_url: str | None
    item_type: str
    published_at: datetime | None
    channel_name: str | None
    subtitle_languages: list[str]


class CreateSourceItemRequest(BaseModel):
    url: HttpUrl


class SourceItemResponse(BaseModel):
    id: int
    source_account_id: int
    external_item_id: str
    item_type: str
    title: str | None
    canonical_url: str | None
    thumbnail_url: str | None
    published_at: datetime | None
    duration_ms: int | None
    status: str

    model_config = ConfigDict(from_attributes=True)  # E2：显式 ConfigDict，与 settings.py 风格一致


def _map_adapter_errors(exc: AdapterError) -> HTTPException:
    """F7：两端点共用的错误映射（400 白名单 / 502 上游失败 / 504 超时）。"""
    if isinstance(exc, (UrlNotAllowedError, NotSingleItemError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AdapterTimeoutError):
        return HTTPException(status_code=504, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


DbDep = Annotated[Session, Depends(get_db)]


def _resolve_adapter(body: ResolveUrlRequest) -> MediaSourceAdapter:
    # per-platform 分流（Task 2 Step 5）：douyin URL → 外部 dtk adapter
    return get_media_adapter(detect_platform(str(body.url)))


def _create_adapter(body: CreateSourceItemRequest) -> MediaSourceAdapter:
    return get_media_adapter(detect_platform(str(body.url)))


RoutedResolveAdapterDep = Annotated[MediaSourceAdapter, Depends(_resolve_adapter)]
RoutedCreateAdapterDep = Annotated[MediaSourceAdapter, Depends(_create_adapter)]


@router.post("/resolve-url", response_model=ResolveUrlResponse)
def resolve_url(body: ResolveUrlRequest, adapter: RoutedResolveAdapterDep) -> ResolveUrlResponse:
    """RAD-022 第一步：解析预览，不落库。"""
    try:
        media = adapter.resolve(str(body.url))
    except AdapterError as exc:
        raise _map_adapter_errors(exc) from exc
    return ResolveUrlResponse(
        platform=media.platform,
        external_id=media.external_item_id,
        title=media.title,
        duration_ms=media.duration_ms,
        thumbnail_url=media.thumbnail_url,
        item_type=media.item_type,
        published_at=media.published_at,
        channel_name=media.channel_name,
        subtitle_languages=[t.language for t in media.subtitles],
    )


@router.post("", response_model=SourceItemResponse, status_code=201)
def create_source_item(
    body: CreateSourceItemRequest, session: DbDep, adapter: RoutedCreateAdapterDep
) -> SourceItemResponse:
    """RAD-022 第二步：确认后创建（服务端重新 resolve，C4）。"""
    try:
        item = discovery.create_item_from_url(str(body.url), session, adapter)
    except AdapterError as exc:
        raise _map_adapter_errors(exc) from exc
    return SourceItemResponse.model_validate(item)


@router.get("")
def list_source_items(
    session: DbDep,
    account_id: int | None = None,
    status: str | None = None,
    asset: bool | None = None,
    limit: int = 100,
):
    """视频库列表（监控台"视频库"页）：按账号/状态/精华过滤，id 倒序。"""
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.settings import get_settings
    from app.db.models import Creator, SourceAccount, SourceItem

    stmt = (
        select(SourceItem, SourceAccount, Creator.display_name)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .order_by(SourceItem.id.desc())
        .limit(min(limit, 500))
    )
    if account_id is not None:
        stmt = stmt.where(SourceItem.source_account_id == account_id)
    if status is not None:
        stmt = stmt.where(SourceItem.status == status)
    if asset is not None:
        stmt = stmt.where(SourceItem.is_asset.is_(asset))
    rows = session.execute(stmt).all()

    # 弹幕计数（Plan #5）：单条聚合查询，避免行级 N+1
    from sqlalchemy import func

    from app.db.models import LiveChatMessage

    item_ids = [item.id for item, _account, _name in rows]
    chat_counts: dict[int, int] = {
        item_id: count
        for item_id, count in session.query(
            LiveChatMessage.source_item_id, func.count(LiveChatMessage.id)
        )
        .filter(LiveChatMessage.source_item_id.in_(item_ids or [0]))
        .group_by(LiveChatMessage.source_item_id)
        .all()
    }

    # 生命周期展示（Plan #6 D7）：精华/非终态无到期；可清理条目 = created_at + TTL
    retention_days = get_settings().content_retention_days
    expiry_eligible = ("transcribed", "failed", "reviewing", "ready")

    def _expires_at(item: SourceItem):
        if item.is_asset or retention_days <= 0 or item.status not in expiry_eligible:
            return None
        return item.created_at + timedelta(days=retention_days)

    return [
        {
            "id": item.id,
            "account_id": item.source_account_id,
            "platform": account.platform,
            "display_name": creator_name,
            "external_item_id": item.external_item_id,
            "title": item.title,
            "item_type": item.item_type,
            "status": item.status,
            "duration_ms": item.duration_ms,
            "published_at": item.published_at,
            "backfill": bool((item.metadata_json or {}).get("backfill")),
            "progress": (item.metadata_json or {}).get("progress"),
            "chat_count": chat_counts.get(item.id, 0),
            "is_asset": item.is_asset,
            "expires_at": _expires_at(item),
        }
        for item, account, creator_name in rows
    ]


@router.post("/{item_id}/prepare", status_code=202)
def prepare_source_item_manual(item_id: int, session: DbDep):
    """手动转录（视频库"转写"按钮）：清 backfill 标记后投递 prepare。

    discovered/failed 可投；其余状态 409（幂等保护，与 prepare 状态门槛一致）。
    """
    from app.db.models import SourceItem
    from app.worker.celery_app import celery_app

    item = session.get(SourceItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    if item.status not in ("discovered", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 {item.status} 不可发起转录（仅 discovered/failed）",
        )
    meta = dict(item.metadata_json or {})
    meta.pop("backfill", None)
    meta.pop("retry", None)  # 人工介入视为重新开始，自动重试退避计数清零
    item.metadata_json = meta
    session.commit()
    celery_app.send_task("prepare_source_item", args=[item_id])
    return {"item_id": item_id, "dispatched": True}


@router.get("/{item_id}/transcript")
def get_transcript(item_id: int, session: DbDep):
    """转录正文（视频库抽屉）：分段文本 + 主播名。未转写/不存在给 4xx。"""
    from app.db.models import Creator, SourceAccount, SourceItem, TranscriptSegment

    row = (
        session.query(SourceItem, SourceAccount, Creator.display_name)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .filter(SourceItem.id == item_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    item, _account, creator_name = row
    segments = (
        session.query(TranscriptSegment)
        .filter(TranscriptSegment.source_item_id == item_id)
        .order_by(TranscriptSegment.sequence_no)
        .all()
    )
    return {
        "id": item.id,
        "title": item.title,
        "display_name": creator_name,
        "segments": [
            {
                "sequence_no": s.sequence_no,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "text": s.text,
            }
            for s in segments
        ],
    }


@router.get("/{item_id}/chat-messages")
def get_chat_messages(item_id: int, session: DbDep, limit: int = 10):
    """直播弹幕详情（视频库抽屉，Plan #5）：只返回前 N 条（默认 10，用户裁决：
    全量展示无意义），total 恒为该会话入库总数。"""
    from sqlalchemy import func

    from app.db.models import LiveChatMessage, SourceItem

    item = session.get(SourceItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    total = (
        session.query(func.count(LiveChatMessage.id))
        .filter(LiveChatMessage.source_item_id == item_id)
        .scalar()
    )
    rows = (
        session.query(
            LiveChatMessage.user_name,
            LiveChatMessage.text,
            LiveChatMessage.published_at,
        )
        .filter(LiveChatMessage.source_item_id == item_id)
        .order_by(LiveChatMessage.id)
        .limit(min(limit, 2000))
        .all()
    )
    return {
        "item_id": item_id,
        "title": item.title,
        "total": total,
        "messages": [
            {"user_name": u, "text": t, "published_at": p} for u, t, p in rows
        ],
    }


@router.post("/{item_id}/extract", status_code=202)
def extract_viewpoints_manual(item_id: int, session: DbDep):
    """手动抽取观点（视频库按钮）：仅 transcribed 状态可发起，幂等由抽取服务保证。"""
    from app.db.models import SourceItem
    from app.worker.celery_app import celery_app

    item = session.get(SourceItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    if item.item_type not in ("vod", "live") or item.status != "transcribed":
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 {item.status} 不可抽取（仅 vod/live + transcribed）",
        )
    celery_app.send_task("extract_source_item_viewpoints", args=[item_id])
    return {"item_id": item_id, "dispatched": True}


@router.get("/{item_id}/viewpoint-evidence")
def get_viewpoint_evidence(item_id: int, session: DbDep):
    """RAD-084：观点证据行（含原文与时间戳，供 seek link 与 review actions）。"""
    from app.db.models import Viewpoint, ViewpointEvidence

    vps = (
        session.query(Viewpoint)
        .filter(Viewpoint.source_item_id == item_id)
        .order_by(Viewpoint.id)
        .all()
    )
    out = []
    for vp in vps:
        evs = (
            session.query(ViewpointEvidence)
            .filter(ViewpointEvidence.viewpoint_id == vp.id)
            .order_by(ViewpointEvidence.evidence_order)
            .all()
        )
        out.append(
            {
                "viewpoint_id": vp.id,
                "claim": vp.claim,
                "stance": vp.stance,
                "verification_status": vp.verification_status,
                "evidences": [
                    {
                        "segment_id": ev.transcript_segment_id,
                        "start_ms": ev.start_ms,
                        "end_ms": ev.end_ms,
                        "text": ev.evidence_text,
                    }
                    for ev in evs
                ],
            }
        )
    return out


@router.post("/{item_id}/asset")
def toggle_asset(item_id: int, session: DbDep, is_asset: bool | None = None):
    """精华资产标记（Plan #6 D6）：is_asset 缺省 = 取反切换；标记后 retention 永不清。"""
    from datetime import UTC, datetime

    from app.db.models import SourceItem

    item = session.get(SourceItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    item.is_asset = (not item.is_asset) if is_asset is None else is_asset
    now = datetime.now(UTC)
    item.asset_at = now if item.is_asset else None
    meta = dict(item.metadata_json or {})
    if item.is_asset:
        meta.pop("unasset_at", None)  # 重新入精华 → 清宽限期
    else:
        # 取消精华 = 宽限期从现在重算（retention 按 max(created_at, unasset_at) 判定）
        meta["unasset_at"] = now.isoformat()
    item.metadata_json = meta
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(item, "metadata_json")
    session.commit()
    return {"item_id": item_id, "is_asset": item.is_asset}


@router.delete("/{item_id}", status_code=200)
def delete_source_item(item_id: int, session: DbDep):
    """删除单条内容（物理，级联转录/观点/资产）+ 写墓碑防 discover 重导。"""
    from app.db.models import DeletedItemRef, SourceItem

    item = session.get(SourceItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    session.add(
        DeletedItemRef(
            source_account_id=item.source_account_id, external_item_id=item.external_item_id
        )
    )
    session.delete(item)
    session.commit()
    return {"deleted": item_id, "tombstoned": True}


class BatchDeleteRequest(BaseModel):
    ids: list[int]


@router.post("/batch-delete", status_code=200)
def batch_delete_items(body: BatchDeleteRequest, session: DbDep):
    """批量物理删除（视频库多选）：逐条级联 + 墓碑，返回成功/失败清单。"""
    from app.db.models import DeletedItemRef, SourceItem

    deleted: list[int] = []
    missing: list[int] = []
    for item_id in body.ids:
        item = session.get(SourceItem, item_id)
        if item is None:
            missing.append(item_id)
            continue
        session.add(
            DeletedItemRef(
                source_account_id=item.source_account_id,
                external_item_id=item.external_item_id,
            )
        )
        session.delete(item)
        deleted.append(item_id)
    session.commit()
    return {"deleted": deleted, "missing": missing}
