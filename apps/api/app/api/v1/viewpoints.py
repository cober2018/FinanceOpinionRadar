"""观点 API（RAD-052 人工复核 + RAD-072 列表过滤）。每次人工修改写 audit_log。"""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Creator, Entity, SourceAccount, SourceItem, Topic, Viewpoint
from app.db.session import get_db
from app.services.audit import write_audit
from app.services.history import build_snapshot

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
        "claim": vp.claim,
        "stance": vp.stance,
        "horizon": vp.horizon,
        "conditional": vp.conditional,
        "importance": float(vp.importance or 0.5),
        "confidence": float(vp.confidence or 0.5),
        "as_of_date": vp.as_of_date,
        "verification_status": vp.verification_status,
        "merge_reason": vp.merge_reason,
    }


class ViewpointPatch(BaseModel):
    claim: str | None = None
    stance: str | None = None
    horizon: str | None = None
    topic_id: int | None = None
    entity_id: int | None = None
    confidence: float | None = None
    importance: float | None = None
    reason: str | None = None


def _get_vp_or_404(session: Session, item_id: int) -> Viewpoint:
    vp = session.get(Viewpoint, item_id)
    if vp is None:
        raise HTTPException(status_code=404, detail=f"viewpoint {item_id} 不存在")
    return vp


def _item_ready_when_no_pending(session: Session, item: SourceItem) -> None:
    """item 无剩余 candidate/needs_review 且状态为 reviewing → ready。"""
    if item.status != "reviewing":
        return
    pending = (
        session.query(func.count(Viewpoint.id))
        .filter(
            Viewpoint.source_item_id == item.id,
            Viewpoint.verification_status.in_(["candidate", "needs_review"]),
        )
        .scalar()
    )
    if not pending:
        from app.domain.pipeline_states import ensure_transition

        ensure_transition(item.status, "ready")
        item.status = "ready"
        session.commit()


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
    vp = _get_vp_or_404(session, viewpoint_id)
    before = {"verification_status": vp.verification_status}
    if vp.verification_status == "confirmed":
        return {"id": vp.id, "verification_status": "confirmed", "already": True}
    vp.verification_status = "confirmed"
    session.commit()
    write_audit(
        session,
        actor="console",
        action="confirm",
        resource_type="viewpoint",
        resource_id=vp.id,
        before=before,
        after={"verification_status": "confirmed"},
        reason=reason,
    )
    if vp.topic_id is not None:
        build_snapshot(session, vp)
    item = session.get(SourceItem, vp.source_item_id)
    if item is not None:
        _item_ready_when_no_pending(session, item)
    session.commit()
    return {"id": vp.id, "verification_status": "confirmed"}


@router.post("/{viewpoint_id}/reject")
def reject_viewpoint(viewpoint_id: int, session: DbDep, reason: str | None = None):
    """RAD-052：人工驳回 → rejected（不删数据）。"""
    vp = _get_vp_or_404(session, viewpoint_id)
    before = {"verification_status": vp.verification_status}
    vp.verification_status = "rejected"
    session.commit()
    write_audit(
        session,
        actor="console",
        action="reject",
        resource_type="viewpoint",
        resource_id=vp.id,
        before=before,
        after={"verification_status": "rejected"},
        reason=reason,
    )
    item = session.get(SourceItem, vp.source_item_id)
    if item is not None:
        _item_ready_when_no_pending(session, item)
    session.commit()
    return {"id": vp.id, "verification_status": "rejected"}


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
    before = {k: getattr(vp, k) for k in updates}
    for k, v in updates.items():
        setattr(vp, k, v)
    session.commit()
    write_audit(
        session,
        actor="console",
        action="patch",
        resource_type="viewpoint",
        resource_id=vp.id,
        before={k: (str(v) if v is not None else None) for k, v in before.items()},
        after={k: (str(v) if v is not None else None) for k, v in updates.items()},
        reason=body.reason,
    )
    session.commit()  # 审计单独落库：write_audit 只 add 不 commit，漏提交会被会话关闭回滚
    return {"id": vp.id, "updated": sorted(updates.keys())}
