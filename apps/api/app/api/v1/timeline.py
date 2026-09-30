"""时间轴（用户 2026-09-30）：当天各主播的事件流，从近到远，纵向时间轴展示。

事件两类：
- viewpoint：一条已确认观点；时刻 = live 用「起播时刻 + 证据在直播内的偏移」
  （主播真正说出这句话的钟点），vod 用发布时刻
- summary：视频观点总结；时刻 = summary_generated_at（直播滚动总结的每次
  更新都刷新这一字段，时间轴上始终展示最新版本）
"""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Creator,
    Entity,
    SourceAccount,
    SourceItem,
    TranscriptSegment,
    Viewpoint,
    ViewpointEvidence,
)
from app.db.session import get_db

router = APIRouter(tags=["timeline"])

DbDep = Annotated[Session, Depends(get_db)]

_MAX_EVENTS = 300


def _day_bounds(day: str) -> tuple[datetime, datetime] | None:
    try:
        start = datetime.fromisoformat(day).astimezone()
    except ValueError:
        return None
    return start, start + timedelta(days=1)


@router.get("/timeline")
def get_timeline(
    session: DbDep,
    date_from: str | None = None,
    date_to: str | None = None,
    event_type: str | None = None,
):
    """事件流（at 倒序）：当天确认观点 + 总结快照；live 观点时刻=起播+证据偏移。"""

    def _published_filters(stmt):
        if date_from:
            b = _day_bounds(date_from)
            if b:
                stmt = stmt.where(SourceItem.published_at >= b[0])
        if date_to:
            b = _day_bounds(date_to)
            if b:
                stmt = stmt.where(SourceItem.published_at < b[1])
        return stmt

    events: list[dict] = []

    # --- 观点事件 ---
    vp_stmt = _published_filters(
        select(
            Viewpoint,
            SourceItem,
            Creator.display_name,
            Creator.avatar_url,
            Entity.canonical_name,
        )
        .join(SourceItem, SourceItem.id == Viewpoint.source_item_id)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .where(Viewpoint.verification_status == "confirmed")
        .order_by(Viewpoint.id.desc())
        .limit(_MAX_EVENTS)
    )
    vp_rows = session.execute(vp_stmt).all()
    # 证据偏移批量取：每条观点取其证据段的最小起点（=直播内的全局偏移）
    vp_ids = [vp.id for vp, *_ in vp_rows]
    first_offset: dict[int, int] = {}
    if vp_ids:
        off_rows = session.execute(
            select(
                ViewpointEvidence.viewpoint_id,
                func.min(TranscriptSegment.start_ms),
            )
            .join(
                TranscriptSegment,
                TranscriptSegment.id == ViewpointEvidence.transcript_segment_id,
            )
            .where(ViewpointEvidence.viewpoint_id.in_(vp_ids))
            .group_by(ViewpointEvidence.viewpoint_id)
        ).all()
        first_offset = {vid: int(seg_start or 0) for vid, seg_start in off_rows}

    for vp, item, creator_name, avatar_url, entity_name in vp_rows:
        at = None
        if item.published_at:
            at = item.published_at
            if item.item_type == "live":
                offset = first_offset.get(vp.id, 0)
                if offset:
                    at = at + timedelta(milliseconds=offset)
        if at is None:
            at = vp.created_at
        events.append(
            {
                "type": "viewpoint",
                "at": at.isoformat(),
                "item_id": item.id,
                "item_type": item.item_type,
                "title": item.title,
                "source_url": item.canonical_url,
                "creator_name": creator_name,
                "avatar_url": avatar_url,
                "viewpoint_id": vp.id,
                "claim": vp.claim,
                "stance": vp.stance,
                "horizon": vp.horizon,
                "entity": entity_name or vp.entity_raw,
                "conditional": vp.conditional,
            }
        )

    # --- 总结事件（含直播滚动总结的最新版本）---
    def _summary_filters(stmt):
        if date_from:
            b = _day_bounds(date_from)
            if b:
                stmt = stmt.where(SourceItem.summary_generated_at >= b[0])
        if date_to:
            b = _day_bounds(date_to)
            if b:
                stmt = stmt.where(SourceItem.summary_generated_at < b[1])
        return stmt

    su_rows = session.execute(
        _summary_filters(
            select(SourceItem, Creator.display_name, Creator.avatar_url)
            .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
            .join(Creator, Creator.id == SourceAccount.creator_id)
            .where(SourceItem.viewpoint_summary.isnot(None))
            .order_by(SourceItem.summary_generated_at.desc())
            .limit(_MAX_EVENTS)
        )
    ).all()
    for item, creator_name, avatar_url in su_rows:
        live_meta = (item.metadata_json or {}).get("live") or {}
        events.append(
            {
                "type": "summary",
                "at": item.summary_generated_at.isoformat() if item.summary_generated_at else None,
                "item_id": item.id,
                "item_type": item.item_type,
                "title": item.title,
                "source_url": item.canonical_url,
                "creator_name": creator_name,
                "avatar_url": avatar_url,
                "summary": item.viewpoint_summary,
                "live_ongoing": item.item_type == "live" and not live_meta.get("closed"),
                "confirmed_count": session.query(Viewpoint.id)
                .filter(
                    Viewpoint.source_item_id == item.id,
                    Viewpoint.verification_status == "confirmed",
                )
                .count(),
            }
        )

    if event_type in ("viewpoint", "summary"):
        events = [e for e in events if e["type"] == event_type]
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {"events": events[:_MAX_EVENTS]}
