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
from app.services.media.factory import get_media_adapter

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


AdapterDep = Annotated[MediaSourceAdapter, Depends(get_media_adapter)]
DbDep = Annotated[Session, Depends(get_db)]


@router.post("/resolve-url", response_model=ResolveUrlResponse)
def resolve_url(body: ResolveUrlRequest, adapter: AdapterDep) -> ResolveUrlResponse:
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
    body: CreateSourceItemRequest, session: DbDep, adapter: AdapterDep
) -> SourceItemResponse:
    """RAD-022 第二步：确认后创建（服务端重新 resolve，C4）。"""
    try:
        item = discovery.create_item_from_url(str(body.url), session, adapter)
    except AdapterError as exc:
        raise _map_adapter_errors(exc) from exc
    return SourceItemResponse.model_validate(item)
