"""视频观点一句话总结（用户 2026-09-24）：confirmed 观点 → LLM 整合 → 写回条目。

触发：最后一个待审观点复核完（item → ready）自动派发；抽屉「立即生成」手动派发。
门禁（所有通道统一）：视频下**无未确认（candidate/needs_review）观点**才允许生成。
rejected（用户 2026-09-26 口径）＝该句不构成观点的抽取误报，不算未确认：不阻塞
生成、也不进入总结内容（取材仍只取 confirmed）。

门禁与 LLM 输入组装在本模块（gate_and_build）；事务协调（模型调用、content 总结
字段写回、ready 事件、提交）在 workflows.summarize_item_viewpoints。
"""

import json

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.runtime.db.models import Entity, SourceItem, Viewpoint

logger = structlog.get_logger(__name__)

SUMMARY_PROMPT_VERSION = "summary@v1"


def unconfirmed_count(session: Session, item_id: int) -> int:
    """未确认观点数（candidate/needs_review）——生成门禁判据。

    rejected 不算：驳回＝该句不构成观点（与 _item_ready_when_no_pending 的
    pending 口径一致），否则一条误报会永久卡死该视频的总结。
    """
    return (
        session.query(func.count(Viewpoint.id))
        .filter(
            Viewpoint.source_item_id == item_id,
            Viewpoint.verification_status.in_(["candidate", "needs_review"]),
        )
        .scalar()
        or 0
    )


def gate_and_build(session: Session, item: SourceItem) -> dict:
    """总结门禁 + LLM 输入组装（纯读：不调用模型、不写库、不提交）。

    返回 {"status": "ok", "system", "user", "schema", "fingerprint"} 或
    {"status": "skipped_not_all_confirmed"|"skipped_no_confirmed", ...}。
    事务协调（模型调用、写回 content 总结字段、ready 事件、提交）在
    workflows.summarize_item_viewpoints。
    """
    from app.modules.insights.llm.registry import get_prompt_registry
    from app.modules.insights.review_eligibility import review_state

    item_id = item.id

    # 门禁：全部观点已确认（无任何未确认观点），且至少有一条
    non_confirmed = unconfirmed_count(session, item_id)
    if non_confirmed:
        return {
            "status": "skipped_not_all_confirmed",
            "non_confirmed": non_confirmed,
        }

    rows = session.execute(
        select(Viewpoint, Entity.canonical_name)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .where(Viewpoint.source_item_id == item_id, Viewpoint.verification_status == "confirmed")
        .order_by(Viewpoint.id)
    ).all()
    if not rows:
        return {"status": "skipped_no_confirmed"}

    _pending, _confirmed, source_fingerprint = review_state(session, item_id)

    pack = get_prompt_registry().get(SUMMARY_PROMPT_VERSION)
    viewpoints_payload = [
        {
            "claim": vp.claim,
            "stance": vp.stance,
            "horizon": vp.horizon,
            "entity": entity_name or vp.entity_raw,
        }
        for vp, entity_name in rows
    ]
    creator_name = _creator_name(session, item)
    user_prompt = (
        pack.user_template.replace("__TITLE__", (item.title or "无标题")[:80])
        .replace("__CREATOR__", creator_name)
        .replace("__VIEWPOINTS__", json.dumps(viewpoints_payload, ensure_ascii=False))
    )
    return {
        "status": "ok",
        "system": pack.system,
        "user": user_prompt,
        "schema": pack.schema,
        "fingerprint": source_fingerprint,
    }


def _creator_name(session: Session, item: SourceItem) -> str:
    from app.runtime.db.models import Creator, SourceAccount

    row = (
        session.query(Creator.display_name)
        .join(SourceAccount, SourceAccount.id == item.source_account_id)
        .filter(SourceAccount.id == item.source_account_id)
        .scalar()
    )
    return row or ""


def dispatch_summarize(item_id: int) -> None:
    """派发总结任务；派发失败只告警（不阻断复核主链，可手动补）。"""
    import structlog

    from app.bootstrap.celery import celery_app

    log = structlog.get_logger(__name__)
    try:
        celery_app.send_task("summarize_source_item_viewpoints", args=[item_id])
        log.info("summarize_dispatched", item_id=item_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("summarize_dispatch_failed", item_id=item_id, error=str(exc)[:150])


def promote_item_if_reviewed(session: Session, item: SourceItem) -> bool:
    """reviewing 且无待审观点（candidate/needs_review）→ ready + 派发总结。

    人工确认/驳回端点、reviewer 自动确认（extraction 收尾）、beat 兜底扫描三条
    路径统一走这里（2026-09-28 实录：大潘/张心朔等直播观点全被 reviewer 自动
    确认，却无人触发收尾检查，条目永卡 reviewing、总结永不生成）。
    """
    if item.status != "reviewing":
        return False
    pending = (
        session.query(func.count(Viewpoint.id))
        .filter(
            Viewpoint.source_item_id == item.id,
            Viewpoint.verification_status.in_(["candidate", "needs_review"]),
        )
        .scalar()
    )
    if pending:
        return False
    from app.runtime.pipeline import ensure_transition

    ensure_transition(item.status, "ready")
    item.status = "ready"
    session.commit()
    dispatch_summarize(item.id)
    return True
