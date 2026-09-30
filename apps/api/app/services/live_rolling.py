"""直播滚动总结（用户 2026-09-30：直播要时效性，不等播完才出观点和总结）。

直播进行中每个分片转写完成即触发：
  增量抽取（extract_live_incremental）——只对「上次抽取水位之后」的新转录段跑
  观点抽取，水位存 metadata_json.live.incremental_extracted_ms，天然幂等；
  → 规则复核 → LLM 复核（复用 EPIC-05 链）→ 滚动总结（rolling_summarize）——
  对当前 confirmed 观点全集重算 viewpoint_summary，直播中每次更新直接覆盖，
  下游（抽屉/开放 API/时间轴）始终可读到最新版本。

会话收尾（close）时若已增量抽取过则不再全文重抽（避免重复观点与 LLM 成本），
走 finalize_after_close 推进状态机并触发收尾总结。
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import func

from app.db.models import SourceItem, Viewpoint

logger = structlog.get_logger(__name__)

# 单次增量抽取的最大转录字符数：超出则本批只取前 N 段（水位停在最后一段的
# end_ms），剩余留给下一轮任务——防御异常超长分片打爆 LLM 上下文
_INCREMENTAL_MAX_CHARS = 30000


def _load_live_meta(item: SourceItem) -> tuple[dict, dict]:
    base_meta = dict(item.metadata_json or {})
    live = dict(base_meta.get("live") or {})
    return base_meta, live


def _persist_live_meta(item: SourceItem, base_meta: dict) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    item.metadata_json = base_meta
    flag_modified(item, "metadata_json")


def extract_live_incremental(session, item_id: int, *, provider=None) -> dict:
    """对水位之后的新转录段跑一轮观点抽取 + 复核派发。

    水位（live.incremental_extracted_ms）之前已抽取的段不再重复抽；无新段
    幂等跳过。收尾按需派发 LLM 复核与滚动总结。
    """
    from app.db.models import TranscriptSegment
    from app.llm.registry import get_prompt_registry
    from app.services.extraction import (
        EXTRACTOR_VERSION,
        PROMPT_VERSION,
        dedupe_source_item_viewpoints,
        normalize_candidates,
        persist_candidate,
    )
    from app.services.reviewer import apply_review_to_candidates, dispatch_llm_review

    item = session.get(SourceItem, item_id)
    if item is None:
        return {"item_id": item_id, "status": "skipped_no_item"}
    if item.item_type != "live":
        return {"item_id": item_id, "status": "skipped_not_live"}
    from app.db.models import SourceAccount

    account = session.get(SourceAccount, item.source_account_id)
    if account is None:
        return {"item_id": item_id, "status": "skipped_no_account"}

    base_meta, live = _load_live_meta(item)
    watermark_ms = int(live.get("incremental_extracted_ms") or 0)
    segments = (
        session.query(TranscriptSegment)
        .filter(
            TranscriptSegment.source_item_id == item_id,
            TranscriptSegment.start_ms >= watermark_ms,
        )
        .order_by(TranscriptSegment.start_ms)
        .all()
    )
    if not segments:
        return {"item_id": item_id, "status": "skipped_no_new_segments", "watermark_ms": watermark_ms}

    # 防超长：本批按段累计截断，水位只推进到本批最后一段
    batch: list = []
    total = 0
    for seg in segments:
        if total and total + len(seg.text or "") > _INCREMENTAL_MAX_CHARS:
            break
        batch.append(seg)
        total += len(seg.text or "")

    seg_map = {seg.id: seg for seg in batch}
    pack = get_prompt_registry().get(PROMPT_VERSION)
    provider = provider or _extraction_provider(session)
    creator_name = _creator_name(session, account)
    full_text = "\n".join(f"[seg:{seg.id}] {seg.text}" for seg in batch)
    user_prompt = (
        pack.user_template.replace("__SEG_COUNT__", str(len(batch)))
        .replace("__FIRST_SEGMENT_ID__", str(batch[0].id))
        .replace("__FULL_TEXT__", full_text)
        .replace("__TITLE__", (item.title or "")[:80])
        .replace("__CREATOR__", creator_name)
    )
    try:
        resp = provider.generate_json(pack.system, user_prompt, pack.schema, temperature=0.2)
    except Exception as exc:  # noqa: BLE001 增量轮失败不影响下一轮（水位不动，重抽兜底）
        logger.warning("live_incremental_llm_failed", item_id=item_id, error=str(exc)[:200])
        return {"item_id": item_id, "status": "llm_failed", "error": str(exc)[:200]}

    candidates = normalize_candidates(resp.data if isinstance(resp.data, dict) else {})
    valid_ids = set(seg_map.keys())
    created_ids: list[int] = []
    rejected = 0
    for cand in candidates if isinstance(candidates, list) else []:
        vp_id, err = persist_candidate(
            session, item, account, cand, seg_map,
            valid_seg_ids=valid_ids,
            extractor_version=EXTRACTOR_VERSION,
            prompt_version=PROMPT_VERSION,
        )
        if err:
            rejected += 1
            continue
        created_ids.append(vp_id)

    dedupe = dedupe_source_item_viewpoints(session, item_id)
    review_counts = apply_review_to_candidates(session, item_id)

    # 水位推进（失败重跑时已落库的观点靠 dedupe 吸收，水位段不重抽）
    live["incremental_extracted_ms"] = max(seg.end_ms for seg in batch)
    live["incremental_last_at"] = datetime.now(UTC).isoformat()
    base_meta["live"] = live
    _persist_live_meta(item, base_meta)
    session.commit()

    if review_counts.get("needs_review"):
        dispatch_llm_review(item_id)
    else:
        # 无需 LLM 复核环节时由这里直接触发滚动总结（有 LLM 复核时由复核收尾触发）
        dispatch_rolling_summarize(item_id)

    logger.info(
        "live_incremental_done",
        item_id=item_id,
        segments=len(batch),
        created=len(created_ids),
        rejected=rejected,
        review=review_counts,
        watermark_ms=live["incremental_extracted_ms"],
    )
    return {
        "item_id": item_id,
        "status": "done",
        "segments": len(batch),
        "created": len(created_ids),
        "rejected": rejected,
        "review": review_counts,
        "watermark_ms": live["incremental_extracted_ms"],
    }


def rolling_summarize_live(session, item_id: int, *, provider=None) -> dict:
    """直播滚动总结：confirmed 观点全集 → 重算 viewpoint_summary 直接覆盖。

    与 summarize_source_item 的区别：不看「全部确认」门禁（直播中总有新候选
    在途）、不推进状态机——纯粹刷新可读的总结文本；confirmed=0 不写。
    """
    import json as _json

    from sqlalchemy import select

    from app.db.models import Entity
    from app.llm.registry import get_prompt_registry
    from app.services.summarizer import SUMMARY_PROMPT_VERSION

    item = session.get(SourceItem, item_id)
    if item is None:
        return {"item_id": item_id, "status": "skipped_no_item"}
    if item.item_type != "live":
        return {"item_id": item_id, "status": "skipped_not_live"}

    rows = session.execute(
        select(Viewpoint, Entity.canonical_name)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .where(Viewpoint.source_item_id == item_id, Viewpoint.verification_status == "confirmed")
        .order_by(Viewpoint.id)
    ).all()
    if not rows:
        return {"item_id": item_id, "status": "skipped_no_confirmed"}

    provider = provider or _extraction_provider(session)
    pack = get_prompt_registry().get(SUMMARY_PROMPT_VERSION)
    from app.services.summarizer import _creator_name

    viewpoints_payload = [
        {
            "claim": vp.claim,
            "stance": vp.stance,
            "horizon": vp.horizon,
            "entity": entity_name or vp.entity_raw,
        }
        for vp, entity_name in rows
    ]
    user_prompt = (
        pack.user_template.replace("__TITLE__", (item.title or "无标题")[:80])
        .replace("__CREATOR__", _creator_name(session, item))
        .replace("__VIEWPOINTS__", _json.dumps(viewpoints_payload, ensure_ascii=False))
    )
    resp = provider.generate_json(pack.system, user_prompt, pack.schema, temperature=0.2)
    data = resp.data if isinstance(resp.data, dict) else {}
    summary = str(data.get("summary") or "").strip()
    if not summary:
        strings = [v for v in data.values() if isinstance(v, str) and v.strip()]
        summary = strings[0].strip() if len(strings) == 1 else ""
    if not summary:
        logger.warning("live_rolling_summary_empty", item_id=item_id, raw_head=getattr(resp, "raw_head", "")[:120])
        return {"item_id": item_id, "status": "skipped_empty_output"}

    item.viewpoint_summary = summary[:500]
    item.summary_generated_at = datetime.now(UTC)
    session.commit()
    logger.info("live_rolling_summary_done", item_id=item_id, confirmed=len(rows), chars=len(summary))
    return {"item_id": item_id, "status": "done", "confirmed": len(rows), "summary": summary}


def finalize_after_close(session, item_id: int) -> dict:
    """已增量抽取过的直播 close 后的收尾：推进状态机 + 统一收尾闸（不全文重抽）。"""
    from app.domain.pipeline_states import ensure_transition
    from app.services.summarizer import promote_item_if_reviewed

    item = session.get(SourceItem, item_id)
    if item is None:
        return {"item_id": item_id, "status": "skipped_no_item"}
    if item.status == "transcribed":
        for step in ("extracting", "reviewing"):
            ensure_transition(item.status, step)
            item.status = step
        session.commit()
    promoted = promote_item_if_reviewed(session, item)
    logger.info("live_finalize_done", item_id=item_id, promoted=promoted, status=item.status)
    return {"item_id": item_id, "status": "done", "promoted": promoted, "item_status": item.status}


def dispatch_extract_live_incremental(item_id: int) -> None:
    from app.worker.celery_app import celery_app

    try:
        celery_app.send_task("extract_live_incremental", args=[item_id])
    except Exception as exc:  # noqa: BLE001 派发失败只告警（下轮扫描重派）
        logger.warning("live_incremental_dispatch_failed", item_id=item_id, error=str(exc)[:150])


def dispatch_rolling_summarize(item_id: int) -> None:
    from app.worker.celery_app import celery_app

    try:
        celery_app.send_task("rolling_summarize_live", args=[item_id])
    except Exception as exc:  # noqa: BLE001
        logger.warning("rolling_summarize_dispatch_failed", item_id=item_id, error=str(exc)[:150])


def _extraction_provider(session):
    from app.services.llm_config import build_llm_provider

    return build_llm_provider(session, task="extraction")


def _creator_name(session, account) -> str:
    from app.db.models import Creator

    row = session.get(Creator, account.creator_id)
    return row.display_name if row else ""
