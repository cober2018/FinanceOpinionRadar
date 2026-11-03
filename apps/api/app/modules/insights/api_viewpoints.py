"""观点 API（RAD-052 人工复核 + RAD-072 列表过滤）。每次人工修改写 audit_log。"""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.runtime.db.models import Creator, Entity, SourceAccount, SourceItem, Topic, Viewpoint
from app.runtime.db.session import get_db

router = APIRouter(prefix="/viewpoints", tags=["viewpoints"])

DbDep = Annotated[Session, Depends(get_db)]

_SORTABLE = {
    "id": Viewpoint.id,
    "confidence": Viewpoint.confidence,
    "importance": Viewpoint.importance,
    "as_of_date": Viewpoint.as_of_date,
}


def _serialize(
    vp: Viewpoint,
    topic: Topic | None,
    entity: Entity | None,
    creator_name: str | None,
    source_url: str | None,
) -> dict[str, Any]:
    return {
        "id": vp.id,
        "creator_id": vp.creator_id,
        "creator_name": creator_name,
        "source_item_id": vp.source_item_id,
        "source_url": source_url,
        "topic_id": vp.topic_id,
        "topic_name": topic.canonical_name if topic else None,
        "entity_id": vp.entity_id,
        "entity_name": entity.canonical_name if entity else None,
        "entity_raw": vp.entity_raw,
        "claim": vp.claim,
        "stance": vp.stance,
        "horizon": vp.horizon,
        "conditional": vp.conditional,
        "importance": float(vp.importance or 0.5),
        "confidence": float(vp.confidence or 0.5),
        "as_of_date": vp.as_of_date,
        "verification_status": vp.verification_status,
        "merge_reason": vp.merge_reason,
        "review_source": vp.review_source,
        "review_note": vp.review_note,
    }


class ViewpointPatch(BaseModel):
    claim: str | None = None
    stance: str | None = None
    horizon: str | None = None
    topic_id: int | None = None
    entity_id: int | None = None
    # 按名称改标的：复用抽取的 normalize_entity（命中→entity_id；未命中→entity_raw
    # 回落并入 candidate 待晋升，与抽取语义一致）；空串=清除标的
    entity_name: str | None = None
    confidence: float | None = None
    importance: float | None = None
    reason: str | None = None


def _get_vp_or_404(session: Session, item_id: int) -> Viewpoint:
    vp = session.get(Viewpoint, item_id)
    if vp is None:
        raise HTTPException(status_code=404, detail=f"viewpoint {item_id} 不存在")
    return vp


@router.get("")
def list_viewpoints(
    session: DbDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort: str = Query("id", pattern="^(id|confidence|importance|as_of_date)$"),
    order: Literal["asc", "desc"] = "desc",
    date_from: date | None = None,
    date_to: date | None = None,
    creator_id: int | None = None,
    topic_id: int | None = None,
    stance: str | None = None,
    confidence_min: float | None = Query(None, ge=0, le=1),
    status: str | None = None,
    source_item_id: int | None = None,
):
    """RAD-072：统一列表过滤 + 分页（items/total/page/page_size）。

    source_item_id：观点页（视频粒度）抽屉按视频取该条全部观点。
    """
    stmt = (
        select(Viewpoint, Topic, Entity, Creator.display_name)
        .join(SourceItem, SourceItem.id == Viewpoint.source_item_id)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == Viewpoint.creator_id)
        .outerjoin(Topic, Topic.id == Viewpoint.topic_id)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .order_by(
            _SORTABLE[sort].desc() if order == "desc" else _SORTABLE[sort].asc(),
            Viewpoint.id.desc(),
        )
    )
    count_stmt = select(func.count(Viewpoint.id))
    filters = []
    if date_from:
        filters.append(Viewpoint.as_of_date >= date_from)
    if date_to:
        filters.append(Viewpoint.as_of_date <= date_to)
    if creator_id:
        filters.append(Viewpoint.creator_id == creator_id)
    if topic_id:
        filters.append(Viewpoint.topic_id == topic_id)
    if stance:
        filters.append(Viewpoint.stance == stance)
    if confidence_min is not None:
        filters.append(Viewpoint.confidence >= confidence_min)
    if status:
        filters.append(Viewpoint.verification_status == status)
    if source_item_id:
        filters.append(Viewpoint.source_item_id == source_item_id)
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


@router.get("/{viewpoint_id}")
def get_viewpoint(viewpoint_id: int, session: DbDep):
    vp = _get_vp_or_404(session, viewpoint_id)
    topic = session.get(Topic, vp.topic_id) if vp.topic_id else None
    entity = session.get(Entity, vp.entity_id) if vp.entity_id else None
    creator = session.get(Creator, vp.creator_id)
    source_item = session.get(SourceItem, vp.source_item_id)
    return _serialize(
        vp,
        topic,
        entity,
        creator.display_name if creator else None,
        source_item.canonical_url if source_item else None,
    )


@router.post("/{viewpoint_id}/confirm")
def confirm_viewpoint(viewpoint_id: int, session: DbDep, reason: str | None = None):
    """RAD-052：人工确认 → confirmed + 快照构建（RAD-060）+ item ready 检查。"""
    from app.modules import workflows

    vp = _get_vp_or_404(session, viewpoint_id)
    return workflows.confirm_viewpoint(session, vp, reason=reason)


@router.post("/{viewpoint_id}/reject")
def reject_viewpoint(viewpoint_id: int, session: DbDep, reason: str | None = None):
    """RAD-052：人工驳回 → rejected（不删数据）。"""
    from app.modules import workflows

    vp = _get_vp_or_404(session, viewpoint_id)
    return workflows.reject_viewpoint(session, vp, reason=reason)


@router.patch("/{viewpoint_id}")
def patch_viewpoint(viewpoint_id: int, body: ViewpointPatch, session: DbDep):
    """RAD-052：人工修正（claim/stance/horizon/topic/entity/置信度等），写审计。"""
    vp = _get_vp_or_404(session, viewpoint_id)
    updates = body.model_dump(exclude_unset=True, exclude={"reason"})
    if not updates:
        raise HTTPException(status_code=422, detail="无修改字段")
    if "stance" in updates and updates["stance"] not in {
        "strong_bullish", "bullish", "neutral", "bearish", "strong_bearish", "unclear",
    }:
        raise HTTPException(status_code=422, detail="stance 非法")
    if (
        updates.get("entity_id") is not None
        and (updates.get("entity_name") or "").strip()
    ):
        raise HTTPException(status_code=422, detail="entity_id 与 entity_name 二选一")
    if "entity_name" in updates:
        from app.modules.insights.entity_normalizer import normalize_entity

        raw = (updates.pop("entity_name") or "").strip()[:200]
        if not raw:
            updates["entity_id"] = None
            updates["entity_raw"] = None
        else:
            eid, _disp = normalize_entity(session, raw)
            updates["entity_id"] = eid
            updates["entity_raw"] = raw
    if updates.get("entity_id") is not None and session.get(Entity, updates["entity_id"]) is None:
        raise HTTPException(status_code=422, detail=f"entity {updates['entity_id']} 不存在")
    from app.modules import workflows

    return workflows.apply_viewpoint_patch(session, vp, updates, reason=body.reason)


def _reextract_item(session: Session, item: SourceItem) -> bool:
    """删旧观点（级联证据）→ 状态回 extracting → 派发抽取任务。返回是否派发。"""
    from app.modules import workflows

    return workflows.reextract_item_viewpoints(session, item)


@router.post("/re-extract/{item_id}", status_code=202)
def reextract_item(item_id: int, session: DbDep):
    """单个视频重抽（v2 整体理解）：删旧观点后重新派发抽取。"""
    item = session.get(SourceItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"source_item {item_id} 不存在")
    if not _reextract_item(session, item):
        raise HTTPException(status_code=409, detail=f"状态 {item.status} 不可重抽")
    return {"item_id": item_id, "dispatched": True}


@router.post("/re-extract-all", status_code=202)
def reextract_all(session: DbDep, scope: str = "v1"):
    """批量重抽：scope=v1 只重抽旧版（extraction@v1）抽过的视频；scope=all 全部可抽的。"""
    stmt = select(SourceItem).where(SourceItem.item_type.in_(["vod", "live"]))
    items = session.execute(stmt).scalars().all()
    dispatched = []
    for item in items:
        if scope == "v1":
            is_v1 = (
                session.query(Viewpoint.id)
                .filter(
                    Viewpoint.source_item_id == item.id,
                    Viewpoint.prompt_version == "extraction@v1",
                )
                .first()
                is not None
            )
            if not is_v1:
                continue  # 未抽过/已是 v2：新引擎会随自动流跑，无需手动派
        if _reextract_item(session, item):
            dispatched.append(item.id)
    return {"dispatched": len(dispatched), "item_ids": dispatched}
