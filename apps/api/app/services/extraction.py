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

from app.db.models import SourceAccount, SourceItem, TranscriptSegment, Viewpoint, ViewpointEvidence
from app.domain.enums import Horizon, Stance
from app.domain.pipeline_states import ensure_transition
from app.domain.transcript.chunker import chunk_transcript
from app.services.entity_normalizer import (
    dedupe_source_item_viewpoints,
    normalize_entity,
    resolve_topic,
)

logger = structlog.get_logger(__name__)

EXTRACTOR_VERSION = "v2"
PROMPT_VERSION = "extraction@v2"
# v2 整体理解：全文单次抽取的上限（≈50 分钟口播）。超长转写（罕见，长直播）
# 回落 v1 chunked 路径（prompt_version 记实际使用的版本，幂等两者都算已抽）。
PROMPT_VERSION_CHUNKED = "extraction@v1"
_WHOLE_DOC_MAX_CHARS = 20_000

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

        # ADR-0004 幂等：已有观点（v2 全文版 / v1 chunked 版）→ 跳过，不重复抽
        existing = (
            session.query(Viewpoint)
            .filter(
                Viewpoint.source_item_id == item_id,
                Viewpoint.prompt_version.in_(
                    [prompt_version, PROMPT_VERSION_CHUNKED]
                ),
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
        provider = provider or _default_provider(session)

        # 状态推进：transcribed → extracting；
        # extracting（重试场景，如 worker 中途被杀）容忍继续，其他状态拒绝
        if item.status == "transcribed":
            ensure_transition(item.status, "extracting")
            item.status = "extracting"
            session.commit()
        elif item.status != "extracting":
            return {"item_id": item_id, "skipped": f"status {item.status}"}

        run_id = str(uuid.uuid4())
        seg_dicts = [
            {"id": seg.id, "start_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.text}
            for seg in segments
        ]

        # v2 整体理解主路径：全文单次抽取（多维立场在完整语境中成形）；
        # 超长转写回落 v1 chunked（局部语境，跨块结构靠去重兜底）
        total_chars = sum(len(str(s["text"])) for s in seg_dicts)
        whole_doc = total_chars <= _WHOLE_DOC_MAX_CHARS
        if whole_doc:
            from app.db.models import Creator

            creator_row = session.get(Creator, account.creator_id)
            creator_name = creator_row.display_name if creator_row else ""
            full_text = "\n".join(f"[seg:{s['id']}] {s['text']}" for s in seg_dicts)
            user_prompt = pack.user_template.replace(
                "__SEG_COUNT__", str(len(seg_dicts))
            ).replace("__FIRST_SEGMENT_ID__", str(seg_dicts[0]["id"])).replace(
                "__FULL_TEXT__", full_text
            ).replace("__TITLE__", (item.title or "")[:80]).replace("__CREATOR__", creator_name)
            llm_calls = [(f"whole:{run_id[:8]}", user_prompt, set(seg_map.keys()))]
            used_prompt_version = prompt_version
        else:
            chunks = chunk_transcript(seg_dicts)
            chunked_pack = registry.get(PROMPT_VERSION_CHUNKED)
            llm_calls = [
                (
                    chunk.chunk_id,
                    chunked_pack.user_template.replace(
                        "__FIRST_SEGMENT_ID__", str(chunk.segment_ids[0])
                    ).replace("__CHUNK_TEXT__", chunk.text),
                    set(chunk.segment_ids),
                )
                for chunk in chunks
            ]
            used_prompt_version = PROMPT_VERSION_CHUNKED

        created_ids: list[int] = []
        rejected: list[dict] = []
        failed_chunks: list[str] = []
        chunk_summaries: list[dict] = []
        for call_id, user_prompt, chunk_ids in llm_calls:
            try:
                resp = provider.generate_json(
                    pack.system if whole_doc else chunked_pack.system,
                    user_prompt,
                    pack.schema,
                    temperature=0.2,
                )
            except Exception as exc:  # noqa: BLE001 chunk 级失败容忍（run 报告记账）
                failed_chunks.append(f"{call_id}: {str(exc)[:120]}")
                continue
            # 模型输出容错：裸数组 / 键名漂移（views/items）都归一到 viewpoints
            data = resp.data if isinstance(resp.data, dict) else {"viewpoints": resp.data or []}
            raw_candidates = data.get("viewpoints")
            if raw_candidates is None:
                raw_candidates = data.get("views") or data.get("items") or data.get("results") or []
            if not isinstance(raw_candidates, list):
                raw_candidates = []
            chunk_summaries.append(
                {
                    "chunk": call_id,
                    "candidates": len(raw_candidates),
                    **({"raw_head": getattr(resp, "raw_head", "")} if not raw_candidates else {}),
                }
            )
            for cand in raw_candidates:
                err, stance = _validate_candidate(cand, chunk_ids)
                if err:
                    rejected.append({"chunk": call_id, "reason": err})
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
                    prompt_version=used_prompt_version,
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
            "mode": "whole_doc" if whole_doc else "chunked",
            "used_prompt_version": used_prompt_version,
            "transcript_chars": total_chars,
            "chunk_summaries": chunk_summaries,
            "provider": type(provider).__name__,
            "chunks": len(llm_calls),
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
        # 状态推进：extracting → reviewing（EPIC-05 审核流从 reviewing 接手）；
        # 抽出 0 观点：没有可审的东西，直接 ready——误标「待审核」会让用户在
        # 观点页找不到这条视频而困惑
        if created_ids:
            ensure_transition(item.status, "reviewing")
            item.status = "reviewing"
        else:
            ensure_transition(item.status, "ready")
            item.status = "ready"
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


def _default_provider(session: Session):
    # EPIC-04+：LLM 配置从设置页（app_setting）读取，模板支持 DeepSeek/GLM/MiniMax 等
    from app.services.llm_config import build_llm_provider

    return build_llm_provider(session)


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
