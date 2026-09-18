"""观点抽取编排（RAD-043）：chunk → LLM → 服务端校验 → 候选落库 → 去重 → 原始 run 存档。

幂等（ADR-0004）：(source_item_id, prompt_version, extractor_version) 已有 viewpoint 即跳过。
单飞：pg advisory lock per item，防 beat 与手动端点并发双跑。
状态机：transcribed → extracting → reviewing（EPIC-05 审核流接手 reviewing）。
校验（执行计划 §7 RAD-043 全清单）：evidence ⊆ chunk、stance/horizon 枚举、
confidence∈[0,1]、claim 非空；无有效证据 → 拒绝该 candidate。
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import SourceAccount, SourceItem, TranscriptSegment, Viewpoint, ViewpointEvidence
from app.domain.enums import Horizon, Stance
from app.domain.pipeline_states import ensure_transition
from app.domain.transcript.chunker import chunk_transcript
from app.llm.provider import MockLLMProvider
from app.services.entity_normalizer import (
    dedupe_source_item_viewpoints,
    normalize_entity,
    resolve_topic,
)

logger = structlog.get_logger(__name__)

EXTRACTOR_VERSION = "v1"
PROMPT_VERSION = "extraction@v1"

_VALID_STANCES = {s.value for s in Stance}
_VALID_HORIZONS = {h.value for h in Horizon}


def _advisory_lock(session: Session, item_id: int) -> bool:
    got = session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": 910000000 + item_id}
    ).scalar()
    session.commit()
    return bool(got)


def _release_lock(session: Session, item_id: int) -> None:
    session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 910000000 + item_id})
    session.commit()


def _validate_candidate(cand: dict, chunk_ids: set[int]) -> tuple[str | None, str]:
    """返回 (错误信息 | None, 规范化后的 stance)。服务端全量校验，不信任模型。"""
    claim = (cand.get("claim") or "").strip()
    if not claim:
        return "claim 为空", ""
    stance = cand.get("stance")
    if stance not in _VALID_STANCES:
        return f"stance 非法: {stance!r}", ""
    confidence = cand.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return f"confidence 越界: {confidence!r}", ""
    horizon = cand.get("horizon") or None
    if horizon is not None and horizon not in _VALID_HORIZONS:
        return f"horizon 非法: {horizon!r}", ""
    evidence = cand.get("evidence_segment_ids") or []
    if not isinstance(evidence, list) or not evidence:
        return "无证据 → 拒绝", ""
    if not set(evidence).issubset(chunk_ids):
        return f"证据越界: {sorted(set(evidence) - chunk_ids)}", ""
    return None, stance


def extract_source_item(
    session: Session,
    item_id: int,
    *,
    provider=None,
    extractor_version: str = EXTRACTOR_VERSION,
    prompt_version: str = PROMPT_VERSION,
) -> dict:
    from app.llm.registry import get_prompt_registry

    if not _advisory_lock(session, item_id):
        logger.info("extract_skipped_singleton", item_id=item_id)
        return {"item_id": item_id, "skipped": "singleton"}
    try:
        item = session.get(SourceItem, item_id)
        if item is None:
            return {"item_id": item_id, "skipped": "not_found"}

        # ADR-0004 幂等：同 (item, prompt_version, extractor_version) 已有观点 → 跳过
        existing = (
            session.query(Viewpoint)
            .filter(
                Viewpoint.source_item_id == item_id,
                Viewpoint.prompt_version == prompt_version,
                Viewpoint.extractor_version == extractor_version,
            )
            .count()
        )
        if existing:
            return {"item_id": item_id, "skipped": "already_extracted", "viewpoints": existing}

        account = session.get(SourceAccount, item.source_account_id)
        if account is None:
            return {"item_id": item_id, "skipped": "no_account"}

        segments = (
            session.query(TranscriptSegment)
            .filter(TranscriptSegment.source_item_id == item_id)
            .order_by(TranscriptSegment.sequence_no)
            .all()
        )
        seg_map = {seg.id: seg for seg in segments}
        if not seg_map:
            return {"item_id": item_id, "skipped": "no_transcript"}

        registry = get_prompt_registry()
        pack = registry.get(prompt_version)
        provider = provider or _default_provider()

        # 状态推进：transcribed → extracting
        ensure_transition(item.status, "extracting")
        item.status = "extracting"
        session.commit()

        run_id = str(uuid.uuid4())
        seg_dicts = [
            {"id": seg.id, "start_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.text}
            for seg in segments
        ]
        chunks = chunk_transcript(seg_dicts)

        created_ids: list[int] = []
        rejected: list[dict] = []
        failed_chunks: list[str] = []
        for chunk in chunks:
            # 模板含 JSON 花括号示例，不能用 str.format——用显式占位符替换
            user_prompt = pack.user_template.replace(
                "__FIRST_SEGMENT_ID__", str(chunk.segment_ids[0])
            ).replace("__CHUNK_TEXT__", chunk.text)
            try:
                resp = provider.generate_json(
                    pack.system, user_prompt, pack.schema, temperature=0.2
                )
            except Exception as exc:  # noqa: BLE001 chunk 级失败容忍（run 报告记账）
                failed_chunks.append(f"{chunk.chunk_id}: {str(exc)[:120]}")
                continue
            chunk_ids = set(chunk.segment_ids)
            for cand in resp.data.get("viewpoints", []):
                err, stance = _validate_candidate(cand, chunk_ids)
                if err:
                    rejected.append({"chunk": chunk.chunk_id, "reason": err})
                    continue
                topic_id = resolve_topic(session, cand.get("topic"))
                entity_id, _disp = None, "none"
                entities_raw = cand.get("entities") or []
                for ent in entities_raw:
                    eid, disp = normalize_entity(
                        session,
                        ent.get("raw_name", ""),
                        entity_type=ent.get("entity_type", "other"),
                        first_seen_item_id=item_id,
                    )
                    if eid is not None:
                        entity_id = eid
                        break
                vp = Viewpoint(
                    creator_id=account.creator_id,
                    source_item_id=item_id,
                    topic_id=topic_id,
                    entity_id=entity_id,
                    claim=cand["claim"].strip(),
                    stance=stance,
                    horizon=cand.get("horizon") or None,
                    conditional=bool(cand.get("conditional", False)),
                    importance=float(cand.get("importance") or 0.5),
                    confidence=float(cand["confidence"]),
                    as_of_date=item.published_at.date() if item.published_at else None,
                    verification_status="candidate",
                    extractor_version=extractor_version,
                    prompt_version=prompt_version,
                )
                session.add(vp)
                session.flush()
                created_ids.append(vp.id)
                order = 1
                for seg_id in dict.fromkeys(cand["evidence_segment_ids"]):
                    seg = seg_map[seg_id]
                    session.add(
                        ViewpointEvidence(
                            viewpoint_id=vp.id,
                            transcript_segment_id=seg.id,
                            start_ms=seg.start_ms,
                            end_ms=seg.end_ms,
                            evidence_text=seg.text,
                            evidence_order=order,
                        )
                    )
                    order += 1
                session.commit()

        dedupe = dedupe_source_item_viewpoints(session, item_id)

        # EPIC-05：规则 reviewer 批量复核 candidate（accept/reject/needs_review）
        from app.services.reviewer import apply_review_to_candidates

        review_counts = apply_review_to_candidates(session, item_id)

        # 原始 run 存档：object://llm-runs/{run_id}.json（D5）
        run_payload = {
            "run_id": run_id,
            "item_id": item_id,
            "prompt_version": prompt_version,
            "extractor_version": extractor_version,
            "provider": type(provider).__name__,
            "chunks": len(chunks),
            "failed_chunks": failed_chunks,
            "rejected": rejected,
            "created_ids": created_ids,
            "dedupe": dedupe,
            "at": datetime.now(UTC).isoformat(),
        }
        run_uri = _store_run(session, run_id, run_payload)
        meta = dict(item.metadata_json or {})
        meta.setdefault("llm_runs", []).append(
            {
                "run_id": run_id,
                "uri": run_uri,
                "prompt_version": prompt_version,
                "extractor_version": extractor_version,
                "created": len(created_ids),
                "rejected": len(rejected),
                "failed_chunks": len(failed_chunks),
                "review": review_counts,
                "at": run_payload["at"],
            }
        )
        item.metadata_json = meta
        flag_modified(item)
        # 状态推进：extracting → reviewing（EPIC-05 审核流从 reviewing 接手）
        ensure_transition(item.status, "reviewing")
        item.status = "reviewing"
        session.commit()

        logger.info(
            "extract_done",
            item_id=item_id,
            created=len(created_ids),
            rejected=len(rejected),
            failed_chunks=len(failed_chunks),
            run_uri=run_uri,
        )
        return {
            "item_id": item_id,
            "created": len(created_ids),
            "rejected": len(rejected),
            "failed_chunks": len(failed_chunks),
            "dedupe": dedupe,
            "review": review_counts,
            "run_uri": run_uri,
        }
    finally:
        _release_lock(session, item_id)


def flag_modified(item):
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(item, "metadata_json")


def _default_provider():
    from app.llm.provider import OpenAICompatProvider

    s = get_settings()
    if not s.llm_api_key or not s.llm_base_url:
        return MockLLMProvider()
    return OpenAICompatProvider(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        model=s.llm_model,
        timeout_sec=s.llm_timeout_sec,
        max_retries=s.llm_max_retries,
    )


def _store_run(session: Session, run_id: str, payload: dict) -> str:
    """原始输出 → object://llm-runs/{run_id}.json；存储不可用时降级为记录引用。"""
    import tempfile

    from app.services.storage import get_storage

    try:
        storage = get_storage()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(payload, f, ensure_ascii=False)
            tmp = Path(f.name)
        return storage.put_file(f"llm-runs/{run_id}.json", tmp, content_type="application/json")
    except Exception as exc:  # noqa: BLE001 存储失败不阻断抽取主链
        logger.warning("llm_run_store_failed", run_id=run_id, error=str(exc)[:150])
        return f"unstored://llm-runs/{run_id}.json"
