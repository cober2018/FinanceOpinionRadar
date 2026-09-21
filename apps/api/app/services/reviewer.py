"""Reviewer Agent（RAD-050/051）：规则版候选观点复核。

V1 无 LLM 依赖（provider 就绪后可升级 LLM reviewer）：
- 证据充分性：≥1 条证据 且 证据总字数 ≥ review_min_evidence_chars
- 转述启发式：claim 命中转述词表（"有人说/据说/网友/市场传闻"等）→ is_quoted_other_person
- 裁决：confidence ≥ 阈值 且 stance≠unclear 且证据充分 且非转述 → accept
        证据完全缺失 → reject（理论上抽取层已拒，双保险）
        其余 → needs_review（进人工队列）
入队条件可配置（RAD-051）：review_confidence_threshold / review_min_evidence_chars 等阈值。
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import Viewpoint, ViewpointEvidence

DEFAULT_QUOTED_MARKERS = ("有人说", "据说", "网友", "市场传闻", "有消息称", "据悉")


@dataclass(frozen=True)
class ReviewDecision:
    decision: str  # accept | reject | needs_review
    evidence_sufficient: bool
    is_quoted_other_person: bool
    corrected_stance: str | None = None
    corrected_horizon: str | None = None
    reasons: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.reasons is None:
            object.__setattr__(self, "reasons", [])


def _evidence_chars(session: Session, viewpoint_id: int) -> tuple[int, int]:
    rows = (
        session.query(ViewpointEvidence)
        .filter(ViewpointEvidence.viewpoint_id == viewpoint_id)
        .all()
    )
    return len(rows), sum(len(ev.evidence_text or "") for ev in rows)


def _thresholds(session: Session) -> tuple[float, int]:
    """阈值：设置页（DB security 键）优先，回落 env 默认。"""
    from app.services.live_status import get_security_settings

    sec = get_security_settings(session)
    eff = sec.get("effective") or {}
    thr = eff.get("review_confidence_threshold")
    chars = eff.get("review_min_evidence_chars")
    return (
        float(thr) if thr is not None else get_settings().review_confidence_threshold,
        int(chars) if chars is not None else get_settings().review_min_evidence_chars,
    )


def review_candidate(session: Session, viewpoint: Viewpoint) -> ReviewDecision:
    threshold, min_chars = _thresholds(session)
    markers = DEFAULT_QUOTED_MARKERS

    ev_count, ev_chars = _evidence_chars(session, viewpoint.id)
    evidence_sufficient = ev_count >= 1 and ev_chars >= min_chars
    claim = viewpoint.claim or ""
    is_quoted = any(m in claim for m in markers)

    reasons: list[str] = []
    if ev_count == 0:
        return ReviewDecision("reject", False, is_quoted, reasons=["无证据"])
    if not evidence_sufficient:
        reasons.append(f"证据不足（{ev_count} 条 / {ev_chars} 字 < {min_chars}）")
    if is_quoted:
        reasons.append("疑似转述他人观点")
    if viewpoint.stance == "unclear":
        reasons.append("立场不明")

    if (
        viewpoint.confidence is not None
        and float(viewpoint.confidence) >= threshold
        and evidence_sufficient
        and not is_quoted
        and viewpoint.stance != "unclear"
    ):
        return ReviewDecision("accept", evidence_sufficient, is_quoted, reasons=reasons)
    if not evidence_sufficient and is_quoted:
        return ReviewDecision(
            "reject", evidence_sufficient, is_quoted, reasons=reasons + ["证据不足且疑似转述"]
        )
    return ReviewDecision("needs_review", evidence_sufficient, is_quoted, reasons=reasons)


def apply_review_to_candidates(session: Session, item_id: int) -> dict:
    """抽取完成后批量复核：accept→confirmed，reject→rejected，其余→needs_review。"""
    counts = {"confirmed": 0, "rejected": 0, "needs_review": 0}
    candidates = (
        session.query(Viewpoint)
        .filter(Viewpoint.source_item_id == item_id, Viewpoint.verification_status == "candidate")
        .all()
    )
    for vp in candidates:
        decision = review_candidate(session, vp)
        key = "confirmed" if decision.decision == "accept" else decision.decision
        vp.verification_status = key
        counts[key] += 1
    session.commit()
    return counts
