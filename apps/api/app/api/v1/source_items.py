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
    limit: int = 100,
):
    """视频库列表（监控台"视频库"页）：按账号/状态过滤，id 倒序。"""
    from sqlalchemy import select

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
    rows = session.execute(stmt).all()
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
