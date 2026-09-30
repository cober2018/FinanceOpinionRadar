"""Reviewer Agent（RAD-050/051）：规则版候选观点复核。

V1 无 LLM 依赖（provider 就绪后可升级 LLM reviewer）：
- 证据充分性：≥1 条证据 且 证据总字数 ≥ review_min_evidence_chars
- 转述启发式：claim 命中转述词表（"有人说/据说/网友/市场传闻"等）→ is_quoted_other_person
- 裁决：confidence ≥ 阈值 且 stance≠unclear 且证据充分 且非转述 → accept
        证据完全缺失 → reject（理论上抽取层已拒，双保险）
        其余 → needs_review（进人工队列）
入队条件可配置（RAD-051）：review_confidence_threshold / review_min_evidence_chars 等阈值。
"""

import structlog
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import Viewpoint, ViewpointEvidence

logger = structlog.get_logger(__name__)

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
        if decision.decision == "accept":
            vp.review_source = "rule"
        counts[key] += 1
    session.commit()
    return counts


# --- EPIC-05：LLM 复核（v2 reviewer，2026-09-30）---
# 分级复核第二道：规则没放行的（needs_review）送 LLM 逐条裁决。保守设计：
# LLM 只有放行权（accept→confirmed），驳回权保留给人工——任何疑虑留队列，
# 错放的代价（污染统计/推送）大于多留人工的一次点击。

REVIEW_PROMPT_VERSION = "review@v1"
_LLM_REVIEW_BATCH = 40  # 单次调用最多送多少条（防超长上下文/截断）

_VALID_STANCES = {"strong_bullish", "bullish", "neutral", "bearish", "strong_bearish", "unclear"}
_VALID_HORIZONS = {"intraday", "1-3D", "1-4W", "1-3M", "3M+"}


def llm_review_enabled(session: Session) -> bool:
    """开关：security 设置（设置页），默认开；关闭即回到纯规则复核。"""
    from app.services.live_status import get_security_settings

    eff = (get_security_settings(session).get("effective") or {}).get("llm_review_enabled")
    return eff is not False


def dispatch_llm_review(item_id: int) -> None:
    """派发 LLM 复核任务；派发失败只告警（beat 兜底会再补）。"""
    import structlog

    from app.worker.celery_app import celery_app

    log = structlog.get_logger(__name__)
    try:
        celery_app.send_task("llm_review_source_item", args=[item_id])
        log.info("llm_review_dispatched", item_id=item_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("llm_review_dispatch_failed", item_id=item_id, error=str(exc)[:150])


def _pending_payload_rows(session: Session, item_id: int) -> list[Viewpoint]:
    """待 LLM 复核的候选（needs_review；candidate 属规则复核前置阶段，不在这里）。"""
    return (
        session.query(Viewpoint)
        .filter(
            Viewpoint.source_item_id == item_id,
            Viewpoint.verification_status == "needs_review",
        )
        .order_by(Viewpoint.id)
        .all()
    )


def _viewpoint_input_rows(session: Session, viewpoints: list[Viewpoint]) -> list[dict]:
    from app.db.models import Entity, ViewpointEvidence

    ids = [vp.id for vp in viewpoints]
    names: dict[int, str] = {}
    entity_ids = [vp.entity_id for vp in viewpoints if vp.entity_id]
    if entity_ids:
        names = dict(
            session.query(Entity.id, Entity.canonical_name)
            .filter(Entity.id.in_(entity_ids))
            .all()
        )
    ev_rows = (
        session.query(ViewpointEvidence.viewpoint_id, ViewpointEvidence.evidence_text)
        .filter(ViewpointEvidence.viewpoint_id.in_(ids))
        .order_by(ViewpointEvidence.viewpoint_id, ViewpointEvidence.evidence_order)
        .all()
    )
    evidence: dict[int, list[str]] = {}
    for vp_id, text in ev_rows:
        if text:
            evidence.setdefault(vp_id, []).append(text[:300])
    return [
        {
            "id": vp.id,
            "claim": vp.claim,
            "stance": vp.stance,
            "horizon": vp.horizon,
            "entity": names.get(vp.entity_id) or vp.entity_raw or "",
            "confidence": float(vp.confidence),
            "evidence": evidence.get(vp.id, []),
        }
        for vp in viewpoints
    ]


def llm_review_pending(session: Session, item_id: int, *, provider=None) -> dict:
    """LLM 逐条复核 needs_review 候选：accept→confirmed（可带立场/期限修正），
    human→留队列并落 LLM 意见。收尾走 promote_item_if_reviewed 统一闸。"""
    import json as _json

    from app.db.models import SourceItem
    from app.llm.calibration import REVIEW_CALIBRATION_MD
    from app.llm.registry import get_prompt_registry
    from app.services.llm_config import build_llm_provider, get_review_model
    from app.services.summarizer import promote_item_if_reviewed

    item = session.get(SourceItem, item_id)
    if item is None:
        return {"item_id": item_id, "status": "skipped_no_item"}
    if not llm_review_enabled(session):
        return {"item_id": item_id, "status": "skipped_disabled"}

    pending = _pending_payload_rows(session, item_id)
    if not pending:
        return {"item_id": item_id, "status": "skipped_no_pending"}

    provider = provider or build_llm_provider(session)
    review_model = get_review_model(session)
    pack = get_prompt_registry().get(REVIEW_PROMPT_VERSION)
    system_prompt = pack.system.replace("__STANCE_CALIBRATION__", REVIEW_CALIBRATION_MD)

    confirmed = corrected = 0
    for start in range(0, len(pending), _LLM_REVIEW_BATCH):
        chunk = pending[start : start + _LLM_REVIEW_BATCH]
        rows = _viewpoint_input_rows(session, chunk)
        user_prompt = pack.user_template.replace(
            "__VIEWPOINTS__", _json.dumps(rows, ensure_ascii=False)
        )
        resp = provider.generate_json(
            system_prompt, user_prompt, pack.schema, model=review_model, temperature=0.2
        )
        reviews = resp.data.get("reviews") if isinstance(resp.data, dict) else None
        if not isinstance(reviews, list):
            logger.warning("llm_review_bad_shape", item_id=item_id, raw_head=getattr(resp, "raw_head", "")[:120])
            continue
        by_id = {vp.id: vp for vp in chunk}
        for r in reviews:
            if not isinstance(r, dict):
                continue
            # 证据 id 字符串化同款坑：MiniMax 常把 id 输出成字符串
            try:
                vid = int(r.get("id"))
            except (TypeError, ValueError):
                continue
            vp = by_id.get(vid)
            if vp is None or vp.verification_status != "needs_review":
                continue  # 越界/已被人先动的忽略
            note = str(r.get("reason") or "").strip()[:500] or None
            if str(r.get("verdict") or "").strip().lower() == "accept":
                vp.verification_status = "confirmed"
                vp.review_source = "llm"
                vp.review_note = note
                c_stance = str(r.get("corrected_stance") or "").strip()
                if c_stance in _VALID_STANCES and c_stance != vp.stance:
                    vp.stance = c_stance
                    corrected += 1
                c_horizon = str(r.get("corrected_horizon") or "").strip()
                if c_horizon in _VALID_HORIZONS and c_horizon != vp.horizon:
                    vp.horizon = c_horizon
                    corrected += 1
                confirmed += 1
            else:
                # 留人工：意见落库给队列展示；review_source 不写（人工裁决后归 manual）
                vp.review_note = note
        session.commit()

    promoted = promote_item_if_reviewed(session, item)
    logger.info(
        "llm_review_done",
        item_id=item_id,
        pending=len(pending),
        confirmed=confirmed,
        corrected=corrected,
        promoted=promoted,
    )
    return {
        "item_id": item_id,
        "status": "done",
        "pending": len(pending),
        "confirmed": confirmed,
        "corrected": corrected,
        "promoted": promoted,
    }
