"""观点历史与共识（EPIC-06：RAD-060/061/062）。

- build_snapshot：confirmed 视角 vs 该 creator+topic 上一条正式视角 → change_type → upsert 快照
- build_topic_consensus：每 topic 每日每 creator 取最新有效视角 → 共识指标
  （confirmed、未过期、非 unclear）
- apply_expiry：horizon 有效期过后将 snapshot 标 expired（不删数据）
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CreatorTopicSnapshot, TopicConsensusDaily, Viewpoint

CONSENSUS_RULE_VERSION = "v1"

# RAD-062：horizon → 默认有效天数（3M+ 可配，默认 180）
HORIZON_VALIDITY_DAYS = {
    "intraday": 1,
    "1-3D": 3,
    "1-4W": 28,
    "1-3M": 90,
    "3M+": 180,
}


def compute_change_type(prev: Viewpoint | None, curr: Viewpoint) -> str:
    """RAD-060 规则（unit test 全覆盖）：
    无前序 → new_thesis；stance 变 → stance_flip；stance 同强度变 → strengthening/weakening；
    仅 horizon 变 → horizon_change；全同 → repeated。unclear 前序视同无前序。
    """
    if prev is None or prev.stance == "unclear":
        return "new_thesis"
    if _base(prev.stance) != _base(curr.stance):
        return "stance_flip"
    prev_h, curr_h = prev.horizon, curr.horizon
    if prev_h != curr_h:
        return "horizon_change"
    prev_strength = _strength(prev.stance)
    curr_strength = _strength(curr.stance)
    if curr_strength > prev_strength:
        return "strengthening"
    if curr_strength < prev_strength:
        return "weakening"
    return "repeated"


_STRENGTH = {"strong_bearish": -2, "bearish": -1, "neutral": 0, "bullish": 1, "strong_bullish": 2}


def _strength(stance: str) -> int:
    return _STRENGTH.get(stance, 0)


def _base(stance: str) -> str:
    """剥强度：strong_bullish/bullish → bullish（方向级比较，PRD 示例 strengthening 语义）"""
    return stance.removeprefix("strong_")


def build_snapshot(
    session: Session, viewpoint: Viewpoint, *, actor: str = "system"
) -> CreatorTopicSnapshot:
    """confirmed 视角落库后调用：找同 creator+topic 上一条正式观点 → change_type → upsert 快照。"""
    topic_id = viewpoint.topic_id
    if topic_id is None:
        # 无主题的观点不进快照层（共识按主题聚合）
        raise ValueError("viewpoint 缺少 topic_id，无法构建快照")
    snap_date = viewpoint.as_of_date or date.today()
    prev = (
        session.query(Viewpoint)
        .filter(
            Viewpoint.creator_id == viewpoint.creator_id,
            Viewpoint.topic_id == topic_id,
            Viewpoint.id != viewpoint.id,
            Viewpoint.verification_status.in_(["confirmed", "ready"]),
            Viewpoint.as_of_date <= snap_date,
        )
        .order_by(Viewpoint.as_of_date.desc(), Viewpoint.id.desc())
        .first()
    )
    change = compute_change_type(prev, viewpoint)
    snap = session.get(CreatorTopicSnapshot, (viewpoint.creator_id, topic_id, snap_date))
    if snap is None:
        snap = CreatorTopicSnapshot(
            creator_id=viewpoint.creator_id,
            topic_id=topic_id,
            snapshot_date=snap_date,
        )
        session.add(snap)
    snap.latest_viewpoint_id = viewpoint.id
    snap.stance = viewpoint.stance
    snap.horizon = viewpoint.horizon
    snap.confidence = float(viewpoint.confidence or 0.5)
    snap.change_type = change
    session.commit()
    return snap


def viewpoint_expired(vp: Viewpoint, today: date | None = None) -> bool:
    ref = today or date.today()
    if vp.verification_status != "confirmed" or vp.as_of_date is None:
        return False
    days = HORIZON_VALIDITY_DAYS.get(vp.horizon or "", 90)
    return ref > vp.as_of_date + timedelta(days=days)


def apply_expiry(session: Session, today: date | None = None) -> int:
    """RAD-062：过期 confirmed 视角 → 其快照标 expired（不删数据）。"""
    ref = today or date.today()
    marked = 0
    vps = session.scalars(
        select(Viewpoint).where(Viewpoint.verification_status == "confirmed")
    ).all()
    for vp in vps:
        if not viewpoint_expired(vp, ref) or vp.topic_id is None:
            continue
        snap = session.get(CreatorTopicSnapshot, (vp.creator_id, vp.topic_id, vp.as_of_date))
        if snap is not None and snap.latest_viewpoint_id == vp.id and snap.change_type != "expired":
            snap.change_type = "expired"
            marked += 1
    session.commit()
    return marked


def build_topic_consensus(session: Session, topic_id: int, trade_date: date) -> TopicConsensusDaily:
    """RAD-061：每 creator 取该日（含）之前最新有效视角（confirmed、非 expired、非 unclear）。"""
    rows = (
        session.query(Viewpoint)
        .filter(
            Viewpoint.topic_id == topic_id,
            Viewpoint.verification_status == "confirmed",
            Viewpoint.stance != "unclear",
            Viewpoint.as_of_date <= trade_date,
        )
        .order_by(Viewpoint.creator_id, Viewpoint.as_of_date.desc(), Viewpoint.id.desc())
        .all()
    )
    latest_per_creator: dict[int, Viewpoint] = {}
    for vp in rows:  # 排序后首见即最新
        latest_per_creator.setdefault(vp.creator_id, vp)
    # 过滤过期
    active = {
        cid: vp
        for cid, vp in latest_per_creator.items()
        if not viewpoint_expired(vp, trade_date)
    }
    counts = {"bullish": 0, "neutral": 0, "bearish": 0}
    confidences: list[float] = []
    for vp in active.values():
        if vp.stance in ("bullish", "strong_bullish"):
            counts["bullish"] += 1
        elif vp.stance in ("bearish", "strong_bearish"):
            counts["bearish"] += 1
        else:
            counts["neutral"] += 1
        confidences.append(float(vp.confidence or 0.5))

    total = len(active)
    bull_ratio = counts["bullish"] / total if total else None
    net = (counts["bullish"] - counts["bearish"]) / total if total else None
    disagreement = 1 - abs(net) if net is not None else None
    avg_conf = sum(confidences) / len(confidences) if confidences else None

    row = session.get(TopicConsensusDaily, (topic_id, trade_date))
    if row is None:
        row = TopicConsensusDaily(topic_id=topic_id, trade_date=trade_date)
        session.add(row)
    row.creator_count = total
    row.bullish_count = counts["bullish"]
    row.neutral_count = counts["neutral"]
    row.bearish_count = counts["bearish"]
    row.bullish_ratio = bull_ratio
    row.net_stance_score = net
    row.disagreement_score = disagreement
    row.confidence = avg_conf
    session.commit()
    return row
