"""视频观点一句话总结（用户 2026-09-24）：confirmed 观点 → LLM 整合 → 写回条目。

触发：最后一个待审观点复核完（item → ready）自动派发；观点抽屉「重新总结」手动
派发。任务幂等（重复执行只覆盖旧总结），无 confirmed 观点跳过不清旧值。
"""

import json
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Entity, SourceItem, Viewpoint

logger = structlog.get_logger(__name__)

SUMMARY_PROMPT_VERSION = "summary@v1"


def summarize_source_item(session: Session, item_id: int, *, provider=None) -> dict:
    """生成并写回一句话总结。返回 {status: done|skipped_no_confirmed|skipped_no_item, summary?}。"""
    from app.llm.registry import get_prompt_registry
    from app.services.llm_config import build_llm_provider

    item = session.get(SourceItem, item_id)
    if item is None:
        return {"item_id": item_id, "status": "skipped_no_item"}

    rows = session.execute(
        select(Viewpoint, Entity.canonical_name)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .where(Viewpoint.source_item_id == item_id, Viewpoint.verification_status == "confirmed")
        .order_by(Viewpoint.id)
    ).all()
    if not rows:
        return {"item_id": item_id, "status": "skipped_no_confirmed"}

    provider = provider or build_llm_provider(session)
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
    resp = provider.generate_json(pack.system, user_prompt, pack.schema, temperature=0.2)
    data = resp.data if isinstance(resp.data, dict) else {}
    summary = str(data.get("summary") or "").strip()
    if not summary:
        # 键名漂移兜底：唯一字符串值直接采纳（MiniMax 对 schema 键名有自由发挥）
        strings = [v for v in data.values() if isinstance(v, str) and v.strip()]
        summary = strings[0].strip() if len(strings) == 1 else ""

    if not summary:
        logger.warning("summarize_empty_output", item_id=item_id, raw_head=getattr(resp, "raw_head", "")[:120])
        return {"item_id": item_id, "status": "skipped_empty_output"}

    item.viewpoint_summary = summary[:500]
    item.summary_generated_at = datetime.now(UTC)
    session.commit()
    logger.info("summarize_done", item_id=item_id, chars=len(summary))
    return {"item_id": item_id, "status": "done", "summary": summary}


def _creator_name(session: Session, item: SourceItem) -> str:
    from app.db.models import Creator, SourceAccount

    row = (
        session.query(Creator.display_name)
        .join(SourceAccount, SourceAccount.creator_id == Creator.id)
        .filter(SourceAccount.id == item.source_account_id)
        .scalar()
    )
    return row or ""


def dispatch_summarize(item_id: int) -> None:
    """派发总结任务；派发失败只告警（不阻断复核主链，可手动补）。"""
    import structlog

    from app.worker.celery_app import celery_app

    log = structlog.get_logger(__name__)
    try:
        celery_app.send_task("summarize_source_item_viewpoints", args=[item_id])
        log.info("summarize_dispatched", item_id=item_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("summarize_dispatch_failed", item_id=item_id, error=str(exc)[:150])
