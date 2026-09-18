"""实体归一化（RAD-044）：确定性四阶匹配，失败入 entity_candidate。

顺序：canonical 精确 → alias → symbol → fuzzy（唯一包含命中）。
只有全部失败才入 entity_candidate（待人工/LLM 晋升）；绝不自动新建正式 Entity。
主题归一同理走 Topic 的 canonical/alias（V1 不建新 topic，未命中返回 None）。
"""

from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Entity, EntityCandidate, Topic


def _norm(s: str) -> str:
    return s.strip().lower()


def normalize_entity(
    session: Session,
    raw_name: str,
    *,
    entity_type: str = "other",
    first_seen_item_id: int | None = None,
) -> tuple[int | None, str]:
    """返回 (entity_id | None, disposition)。disposition: exact/alias/symbol/fuzzy/candidate。"""
    raw = _norm(raw_name)
    if not raw:
        return None, "empty"

    # 1) canonical 精确
    hit = session.scalars(
        select(Entity).where(func.lower(Entity.canonical_name) == raw)
    ).first()
    if hit:
        return hit.id, "exact"

    # 2) alias 包含（数组元素小写比对）
    for e in session.scalars(select(Entity)).all():
        if any(_norm(a) == raw for a in (e.aliases or [])):
            return e.id, "alias"

    # 3) symbol 精确
    hit = session.scalars(select(Entity).where(func.lower(Entity.symbol) == raw)).first()
    if hit:
        return hit.id, "symbol"

    # 4) fuzzy：双向包含的唯一命中
    fuzzy_hits = [
        e
        for e in session.scalars(select(Entity)).all()
        if (raw in _norm(e.canonical_name) or _norm(e.canonical_name) in raw)
        and len(raw) >= 2
    ]
    if len(fuzzy_hits) == 1:
        return fuzzy_hits[0].id, "fuzzy"

    # 全部失败：入候选（幂等 upsert）
    cand = session.scalars(
        select(EntityCandidate).where(
            EntityCandidate.raw_name == raw_name.strip(),
            EntityCandidate.entity_type == entity_type,
        )
    ).first()
    if cand is None:
        session.add(
            EntityCandidate(
                raw_name=raw_name.strip(),
                entity_type=entity_type,
                first_seen_item_id=first_seen_item_id,
            )
        )
        session.flush()
    return None, "candidate"


def resolve_topic(session: Session, raw_topic: str | None) -> int | None:
    """主题归一：canonical/alias 精确；V1 不自动建 topic，未命中返回 None。"""
    if not raw_topic or not raw_topic.strip():
        return None
    raw = _norm(raw_topic)
    for t in session.scalars(select(Topic)).all():
        if _norm(t.canonical_name) == raw or any(_norm(a) == raw for a in (t.aliases or [])):
            return t.id
    return None


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def dedupe_source_item_viewpoints(
    session: Session, item_id: int, *, threshold: float = 0.85
) -> dict:
    """RAD-045 规则去重：同 item 内 entity+stance 相同且 claim 高相似的候选合并。

    保留 id 最小者，吸收其证据行并记 merge_reason；被吸收者删除。
    """
    from app.db.models import Viewpoint, ViewpointEvidence

    candidates = (
        session.query(Viewpoint)
        .filter(Viewpoint.source_item_id == item_id, Viewpoint.verification_status == "candidate")
        .order_by(Viewpoint.id)
        .all()
    )
    merged, absorbed_count = 0, 0
    for i, keeper in enumerate(candidates):
        if keeper.id is None:
            continue
        for other in candidates[i + 1 :]:
            if other.id is None:
                continue
            same_key = keeper.entity_id == other.entity_id and keeper.stance == other.stance
            if not same_key:
                continue
            ratio = _similar(keeper.claim, other.claim)
            if ratio < threshold:
                continue
            # 吸收证据：order 续接 keeper 现有证据
            existing_orders = [
                ev.evidence_order
                for ev in session.query(ViewpointEvidence)
                .filter(ViewpointEvidence.viewpoint_id == keeper.id)
                .all()
            ]
            next_order = (max(existing_orders) if existing_orders else 0) + 1
            for ev in (
                session.query(ViewpointEvidence)
                .filter(ViewpointEvidence.viewpoint_id == other.id)
                .order_by(ViewpointEvidence.evidence_order)
                .all()
            ):
                ev.viewpoint_id = keeper.id
                ev.evidence_order = next_order
                next_order += 1
            keeper.merge_reason = (
                f"merged #{other.id}: 同实体同立场 claim 相似度 {ratio:.2f}"
            )
            session.delete(other)
            absorbed_count += 1
            merged += 1
    session.commit()
    return {"merged": merged, "absorbed": absorbed_count}
