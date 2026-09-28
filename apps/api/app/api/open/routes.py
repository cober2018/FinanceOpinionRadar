"""开放数据 API（/open/v1）：产品矩阵底座对兄弟产品的只读成果输出。

版本承诺：本前缀下的路径与响应结构稳定，破坏性变更走 /open/v2；
默认只出 confirmed 观点——未复核的机器产物不对外。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.open.deps import require_api_key
from app.api.v1.transcripts import _search_impl
from app.api.v1.viewpoints import _serialize
from app.db.models import (
    Creator,
    Entity,
    SourceAccount,
    SourceItem,
    Topic,
    VideoSummaryEvent,
    Viewpoint,
)
from app.db.session import get_db
from app.services.video_summary_feed import current_event, serialize_event, valid_summary_candidates

router = APIRouter(
    prefix="/open/v1",
    tags=["open-api"],
    dependencies=[Depends(require_api_key)],
)

DbDep = Annotated[Session, Depends(get_db)]


@router.get("/viewpoints")
def list_viewpoints(
    session: DbDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    creator_id: int | None = None,
    stance: str | None = None,
    entity_id: int | None = None,
    status: str = Query("confirmed", pattern="^(confirmed|candidate|needs_review|rejected|all)$"),
    since: datetime | None = Query(
        None, description="增量拉取：只返回 updated_at > since 的观点（ISO 时间）"
    ),
):
    """成果主接口：默认只出已确认观点；since 增量、多维过滤、分页。"""
    stmt = (
        select(Viewpoint, Topic, Entity, Creator.display_name)
        .join(Creator, Creator.id == Viewpoint.creator_id)
        .outerjoin(Topic, Topic.id == Viewpoint.topic_id)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .order_by(Viewpoint.updated_at.desc(), Viewpoint.id.desc())
    )
    count_stmt = select(func.count(Viewpoint.id))
    filters = []
    if status != "all":
        filters.append(Viewpoint.verification_status == status)
    if creator_id:
        filters.append(Viewpoint.creator_id == creator_id)
    if stance:
        filters.append(Viewpoint.stance == stance)
    if entity_id:
        filters.append(Viewpoint.entity_id == entity_id)
    if since:
        filters.append(Viewpoint.updated_at > since)
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    total = session.scalar(count_stmt)
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).all()
    return {
        "items": [_serialize(vp, topic, entity, name, None) for vp, topic, entity, name in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/viewpoints/{viewpoint_id}")
def get_viewpoint(viewpoint_id: int, session: DbDep):
    vp = session.get(Viewpoint, viewpoint_id)
    if vp is None:
        raise HTTPException(status_code=404, detail=f"viewpoint {viewpoint_id} 不存在")
    topic = session.get(Topic, vp.topic_id) if vp.topic_id else None
    entity = session.get(Entity, vp.entity_id) if vp.entity_id else None
    creator = session.get(Creator, vp.creator_id)
    return _serialize(vp, topic, entity, creator.display_name if creator else None, None)


@router.get("/items/{item_id}/transcript")
def get_transcript(item_id: int, session: DbDep):
    """转录全文（粗清洗成果）：与控制台同构的分段结构。"""
    from app.db.models import SourceItem, TranscriptSegment

    row = (
        session.query(SourceItem, Creator.display_name)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .filter(SourceItem.id == item_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    item, creator_name = row
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
        "item_type": item.item_type,
        "published_at": item.published_at,
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


@router.get("/items/{item_id}/summary")
def get_item_summary(item_id: int, session: DbDep):
    """视频观点一句话总结（全部复核确认后由 LLM 整合生成）。"""
    from app.db.models import SourceItem

    row = (
        session.query(SourceItem, Creator.display_name)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .filter(SourceItem.id == item_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    item, creator_name = row
    current = current_event(session, item_id)
    latest = session.scalars(
        select(VideoSummaryEvent)
        .where(VideoSummaryEvent.item_id == item_id)
        .order_by(VideoSummaryEvent.version.desc()).limit(1)
    ).first()
    return {
        "item_id": item.id,
        "title": item.title,
        "display_name": creator_name,
        "item_type": item.item_type,
        "published_at": item.published_at,
        "summary": item.viewpoint_summary if current else None,
        "generated_at": item.summary_generated_at if current else None,
        "export_state": "ready" if current else ("withdrawn" if latest else "not_available"),
        "version": current.version if current else None,
        "event_id": f"vsum-{current.id}" if current else None,
        "video_time": current.payload_json.get("video_time") if current else None,
        "time_basis": current.payload_json.get("time_basis") if current else "unknown",
    }


@router.get("/video-summaries")
def list_video_summary_events(
    session: DbDep,
    cursor: int = Query(0, ge=0, description="最后已处理的视频总结事件序号"),
    page_size: int = Query(50, ge=1, le=200),
):
    """一视频一总结的版本变更流；ready 和 withdrawn 使用同一游标。"""
    rows = session.scalars(
        select(VideoSummaryEvent)
        .where(VideoSummaryEvent.id > cursor)
        .order_by(VideoSummaryEvent.id)
        .limit(page_size + 1)
    ).all()
    page = rows[:page_size]
    return {
        "items": [serialize_event(event) for event in page],
        "next_cursor": page[-1].id if page else cursor,
        "has_more": len(rows) > page_size,
    }


@router.get("/broadcasters/recent-summaries")
def list_recent_broadcaster_summaries(session: DbDep):
    accounts = session.scalars(select(SourceAccount).order_by(SourceAccount.id)).all()
    summaries: dict[int, list[dict]] = {account.id: [] for account in accounts}
    latest = (
        select(VideoSummaryEvent.item_id, func.max(VideoSummaryEvent.version).label("version"))
        .group_by(VideoSummaryEvent.item_id).subquery()
    )
    items = session.execute(
        select(SourceItem, VideoSummaryEvent)
        .join(latest, latest.c.item_id == SourceItem.id)
        .join(VideoSummaryEvent, (VideoSummaryEvent.item_id == SourceItem.id)
              & (VideoSummaryEvent.version == latest.c.version))
        .where(VideoSummaryEvent.state == "ready")
        .order_by(SourceItem.source_account_id, SourceItem.published_at.desc().nullslast(),
                  SourceItem.summary_generated_at.desc().nullslast(), SourceItem.id.desc())
    )
    for batch in items.partitions(100):
        candidates = [
            (item, event) for item, event in batch
            if item.source_account_id in summaries
            and len(summaries[item.source_account_id]) < 5
        ]
        valid = valid_summary_candidates(session, candidates)
        for item, event in candidates:
            selected = summaries[item.source_account_id]
            if (
                item.id in valid and len(selected) < 5
                and (event.payload_json.get("summary") or "").strip()
            ):
                selected.append(serialize_event(event))
    return {
        "complete": True,
        "accounts": [{"source_account_id": account.id, "summaries": summaries[account.id]}
                     for account in accounts],
    }


@router.get("/transcripts/search")
def search_transcripts(q: str, session: DbDep):
    """转录关键词搜索：条目级命中（含摘要片段）。"""
    return _search_impl(session, q)


@router.get("/creators")
def list_creators(session: DbDep):
    """主播与账号映射：供下游按平台/账号维度对齐数据。"""
    rows = (
        session.execute(
            select(Creator, SourceAccount)
            .join(SourceAccount, SourceAccount.creator_id == Creator.id)
            .order_by(Creator.id)
        )
    ).all()
    by_creator: dict[int, dict[str, Any]] = {}
    for creator, account in rows:
        entry = by_creator.setdefault(
            creator.id,
            {"creator_id": creator.id, "display_name": creator.display_name, "accounts": []},
        )
        entry["accounts"].append(
            {
                "account_id": account.id,
                "platform": account.platform,
                "external_id": account.external_id,
                "enabled": account.enabled,
            }
        )
    return {"items": list(by_creator.values())}


@router.get("/broadcasters")
def list_broadcasters(session: DbDep):
    """Flat latest snapshot of source accounts for downstream follow directories."""
    rows = session.scalars(
        select(SourceAccount)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .order_by(SourceAccount.id)
    ).all()
    creators = {
        creator.id: creator.display_name
        for creator in session.scalars(select(Creator)).all()
    }
    return {
        "items": [
            {
                "source_account_id": account.id,
                "display_name": creators.get(account.creator_id),
                "platform": account.platform,
                "external_id": account.external_id,
                "enabled": account.enabled,
            }
            for account in rows
        ]
    }


@router.get("/entities")
def list_entities(session: DbDep) -> dict:
    """标的词典：复用控制台 taxonomy 实现（同一数据源）。"""
    from app.api.v1.taxonomy import list_entities as _list

    return _list(session)
