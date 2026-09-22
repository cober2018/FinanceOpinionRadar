"""离线财经观点质量评测。

默认只读取冻结转录、预测和人工裁决，不连接数据库、不调用模型。只有显式传入
``--generate-predictions`` 才从环境变量构造现有 OpenAI 兼容 provider，并将新的
预测运行写入本地文件。工具完成与业务质量通过是两个状态。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCORING_VERSION = "finance-label-v1"
VALID_QUALIFICATIONS = {"demo", "draft", "human_verified"}
VALID_SPLITS = {"development", "acceptance"}
VALID_CONTENT_TYPES = {"live", "short_video", "long_video"}
VALID_DECISIONS = {"pending", "pass", "fail"}
VALID_STANCES = {
    "strong_bullish",
    "bullish",
    "neutral",
    "bearish",
    "strong_bearish",
    "unclear",
}
VALID_HORIZONS = {"intraday", "1-3D", "1-4W", "1-3M", "3M+"}
VALID_STATEMENT_TYPES = {"opinion", "prediction", "reported_claim", "factual_claim", "method"}
VALID_FIELD_STATUSES = {"present", "not_mentioned", "unclear", "not_applicable"}
VALID_TRANSCRIPT_VERIFICATIONS = {"pending", "verified", "rejected"}
VALID_SECONDARY_VERIFICATIONS = {"not_verified", "verified", "not_applicable"}


class EvaluationInputError(ValueError):
    """冻结输入无效，无法安全计算指标。"""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvaluationInputError(f"文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationInputError(f"JSON 无效: {path}: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except FileNotFoundError as exc:
        raise EvaluationInputError(f"文件不存在: {path}") from exc


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_bytes(raw)


def _parse_model_response(raw_content: str) -> Any:
    text = raw_content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lower, upper = text.find("{"), text.rfind("}")
        if lower >= 0 and upper > lower:
            return json.loads(text[lower : upper + 1])
        raise


def _dataset_path(dataset: Path, relative: str) -> Path:
    resolved = (dataset / relative).resolve()
    root = dataset.resolve()
    if resolved != root and root not in resolved.parents:
        raise EvaluationInputError(f"数据集路径越界: {relative}")
    return resolved


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 6) if denominator else None,
    }


def _similar(left: str, right: str) -> float:
    return round(difflib.SequenceMatcher(None, left, right).ratio(), 6)


def _normalize_entities(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names = []
    for entity in value:
        if isinstance(entity, dict):
            name = str(entity.get("raw_name") or "").strip().casefold()
            if name:
                names.append(name)
    return tuple(sorted(set(names)))


def _prompt_assets(version: str) -> dict[str, Any]:
    prompt_dir = REPO / "apps/api/app/llm/prompts" / version
    try:
        system_prompt = (prompt_dir / "system.md").read_text(encoding="utf-8")
        user_template = (prompt_dir / "user_template.md").read_text(encoding="utf-8")
        schema = _read_json(prompt_dir / "schema.json")
    except OSError as exc:
        raise EvaluationInputError(f"提示词版本不可读: {version}") from exc
    return {
        "version": version,
        "system": system_prompt,
        "user_template": user_template,
        "schema": schema,
        "sha256": _json_hash(
            {"system": system_prompt, "user_template": user_template, "schema": schema}
        ),
    }


def _expected_input_plan(context: dict[str, Any]) -> dict[str, Any]:
    transcript = context["transcript"]
    source = context["source"]
    if sum(len(str(segment["text"])) for segment in transcript) <= 20_000:
        assets = _prompt_assets("extraction@v2")
        full_text = "\n".join(f"[seg:{s['id']}] {s['text']}" for s in transcript)
        user_prompt = (
            assets["user_template"].replace("__SEG_COUNT__", str(len(transcript)))
            .replace("__FIRST_SEGMENT_ID__", str(transcript[0]["id"]))
            .replace("__FULL_TEXT__", full_text)
            .replace("__TITLE__", str(source.get("title") or "")[:80])
            .replace("__CREATOR__", str(source.get("creator") or ""))
        )
        return {
            "processing_mode": "whole_doc",
            "units": [
                {
                    "unit_id": "whole-000",
                    "prompt_version": assets["version"],
                    "prompt_sha256": assets["sha256"],
                    "user_prompt_sha256": _sha256_bytes(user_prompt.encode()),
                    "segment_ids": sorted(context["segment_ids"]),
                }
            ],
        }

    assets = _prompt_assets("extraction@v1")
    ordered = sorted(transcript, key=lambda segment: (segment["start_ms"], segment["end_ms"]))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for segment in ordered:
        span = (
            segment["end_ms"] - current[0]["start_ms"]
            if current
            else segment["end_ms"] - segment["start_ms"]
        )
        if current and span > 600_000:
            groups.append(current)
            current = [segment]
            continue
        current.append(segment)
        if span >= 450_000:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    planned_units = []
    for index, group in enumerate(groups):
        carried: list[dict[str, Any]] = []
        previous = groups[index - 1] if index > 0 else None
        previous_oversized = previous is not None and (
            previous[-1]["end_ms"] - previous[0]["start_ms"]
        ) > 600_000
        if previous is not None and not previous_oversized:
            own_ids = {segment["id"] for segment in group}
            carried = [segment for segment in previous[-1:] if segment["id"] not in own_ids]
        merged = carried + group
        user_prompt = assets["user_template"].replace(
            "__FIRST_SEGMENT_ID__", str(merged[0]["id"])
        ).replace("__CHUNK_TEXT__", "\n".join(segment["text"] for segment in merged))
        planned_units.append(
            {
                "unit_id": f"chunk-{index:03d}",
                "prompt_version": assets["version"],
                "prompt_sha256": assets["sha256"],
                "user_prompt_sha256": _sha256_bytes(user_prompt.encode()),
                "segment_ids": sorted(segment["id"] for segment in merged),
            }
        )
    return {"processing_mode": "chunked", "units": planned_units}


def _load_dataset(dataset: Path) -> dict[str, Any]:
    manifest_path = dataset / "sources.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise EvaluationInputError("sources.json 必须是含 cases 数组的新格式")
    dataset_version = str(manifest.get("dataset_version") or "").strip()
    if not dataset_version:
        raise EvaluationInputError("sources.json 缺 dataset_version")

    cases: dict[str, dict[str, Any]] = {}
    reference_ids: set[str] = set()
    annotation_hashes: dict[str, str] = {}
    transcript_hashes: dict[str, str] = {}
    for source in manifest["cases"]:
        if not isinstance(source, dict):
            raise EvaluationInputError("sources.json cases 元素必须是对象")
        case_id = str(source.get("case_id") or "").strip()
        if not case_id or case_id in cases:
            raise EvaluationInputError(f"case_id 缺失或重复: {case_id!r}")
        if source.get("qualification") not in VALID_QUALIFICATIONS:
            raise EvaluationInputError(f"{case_id}: qualification 非法")
        if source.get("split") not in VALID_SPLITS:
            raise EvaluationInputError(f"{case_id}: split 非法")
        if source.get("content_type") not in VALID_CONTENT_TYPES:
            raise EvaluationInputError(f"{case_id}: content_type 非法")
        if not source.get("url") or not source.get("platform") or not source.get("creator"):
            raise EvaluationInputError(f"{case_id}: 来源 URL/platform/creator 不完整")

        transcript_path = _dataset_path(dataset, str(source.get("transcript") or ""))
        transcript_hash = _sha256_file(transcript_path)
        if transcript_hash != source.get("transcript_sha256"):
            expected_hash = source.get("transcript_sha256")
            raise EvaluationInputError(
                f"{case_id}: 转录哈希不匹配，期望 {expected_hash}，实际 {transcript_hash}"
            )
        transcript = _read_json(transcript_path)
        if not isinstance(transcript, list) or not transcript:
            raise EvaluationInputError(f"{case_id}: 转录必须是非空数组")
        segment_ids: set[int] = set()
        segment_map: dict[int, dict[str, Any]] = {}
        for segment in transcript:
            if not isinstance(segment, dict) or not isinstance(segment.get("id"), int):
                raise EvaluationInputError(f"{case_id}: 转录段缺整数 id")
            segment_id = segment["id"]
            if segment_id in segment_ids:
                raise EvaluationInputError(f"{case_id}: 转录段 id 重复: {segment_id}")
            if not isinstance(segment.get("text"), str) or not segment["text"].strip():
                raise EvaluationInputError(f"{case_id}: 转录段 {segment_id} 文本为空")
            if not isinstance(segment.get("start_ms"), int) or not isinstance(
                segment.get("end_ms"), int
            ):
                raise EvaluationInputError(f"{case_id}: 转录段 {segment_id} 时间无效")
            if segment["start_ms"] >= segment["end_ms"]:
                raise EvaluationInputError(f"{case_id}: 转录段 {segment_id} 时间倒置")
            segment_ids.add(segment_id)
            segment_map[segment_id] = segment

        annotation_path = _dataset_path(dataset, str(source.get("annotation") or ""))
        annotation = _read_json(annotation_path)
        if not isinstance(annotation, dict) or annotation.get("case_id") != case_id:
            raise EvaluationInputError(f"{case_id}: annotation case_id 不一致")
        qualification = annotation.get("qualification")
        if (
            qualification != source.get("qualification")
            or qualification not in VALID_QUALIFICATIONS
        ):
            raise EvaluationInputError(f"{case_id}: source 与 annotation 资格不一致")
        viewpoints = annotation.get("viewpoints")
        if not isinstance(viewpoints, list):
            raise EvaluationInputError(f"{case_id}: viewpoints 必须是数组")
        verification = annotation.get("verification")
        if not isinstance(verification, dict):
            raise EvaluationInputError(f"{case_id}: verification 必须是对象")
        if verification.get("transcript_support") not in VALID_TRANSCRIPT_VERIFICATIONS:
            raise EvaluationInputError(f"{case_id}: transcript_support 非法")
        for key in ("audio_video_verified", "external_fact_verified"):
            if verification.get(key) not in VALID_SECONDARY_VERIFICATIONS:
                raise EvaluationInputError(f"{case_id}: {key} 非法")
        for viewpoint in viewpoints:
            if not isinstance(viewpoint, dict):
                raise EvaluationInputError(f"{case_id}: 人工观点必须是对象")
            reference_id = str(viewpoint.get("reference_id") or "").strip()
            if not reference_id or reference_id in reference_ids:
                raise EvaluationInputError(f"{case_id}: reference_id 缺失或重复: {reference_id!r}")
            reference_ids.add(reference_id)
            if not str(viewpoint.get("claim") or "").strip():
                raise EvaluationInputError(f"{reference_id}: claim 为空")
            if viewpoint.get("statement_type") not in VALID_STATEMENT_TYPES:
                raise EvaluationInputError(f"{reference_id}: statement_type 非法")
            if viewpoint.get("stance") not in VALID_STANCES:
                raise EvaluationInputError(f"{reference_id}: stance 非法")
            horizon = viewpoint.get("horizon")
            if horizon is not None and horizon not in VALID_HORIZONS:
                raise EvaluationInputError(f"{reference_id}: horizon 非法")
            if not isinstance(viewpoint.get("conditional"), bool):
                raise EvaluationInputError(f"{reference_id}: conditional 必须是布尔值")
            if viewpoint["conditional"] and not str(viewpoint.get("condition_text") or "").strip():
                raise EvaluationInputError(f"{reference_id}: 条件观点缺 condition_text")
            if not isinstance(viewpoint.get("entities"), list):
                raise EvaluationInputError(f"{reference_id}: entities 必须是数组")
            if any(
                not isinstance(entity, dict) or not str(entity.get("raw_name") or "").strip()
                for entity in viewpoint["entities"]
            ):
                raise EvaluationInputError(f"{reference_id}: entities 元素非法")
            topic = viewpoint.get("topic")
            if topic is not None and not isinstance(topic, str):
                raise EvaluationInputError(f"{reference_id}: topic 非法")
            speaker = viewpoint.get("speaker")
            if speaker is not None and not isinstance(speaker, str):
                raise EvaluationInputError(f"{reference_id}: speaker 非法")
            field_status = viewpoint.get("field_status")
            if not isinstance(field_status, dict) or any(
                status not in VALID_FIELD_STATUSES for status in field_status.values()
            ):
                raise EvaluationInputError(f"{reference_id}: field_status 非法")
            evidence = viewpoint.get("evidence_segment_ids")
            if not isinstance(evidence, list) or not all(isinstance(v, int) for v in evidence):
                raise EvaluationInputError(f"{reference_id}: evidence_segment_ids 非法")
            if not set(evidence).issubset(segment_ids):
                raise EvaluationInputError(f"{reference_id}: 人工证据越界")
            if qualification == "human_verified" and not evidence:
                raise EvaluationInputError(f"{reference_id}: human_verified 观点不得缺证据")
            evidence_references = viewpoint.get("evidence_references")
            if qualification == "human_verified":
                if not isinstance(evidence_references, list) or not evidence_references:
                    raise EvaluationInputError(
                        f"{reference_id}: human_verified 缺 evidence_references"
                    )
                referenced_ids: set[int] = set()
                for reference in evidence_references:
                    if not isinstance(reference, dict):
                        raise EvaluationInputError(f"{reference_id}: evidence_references 非法")
                    segment_id = reference.get("segment_id")
                    if segment_id not in segment_map:
                        raise EvaluationInputError(f"{reference_id}: 证据段不存在: {segment_id}")
                    segment = segment_map[segment_id]
                    excerpt = str(reference.get("excerpt") or "").strip()
                    if not excerpt or excerpt not in segment["text"]:
                        raise EvaluationInputError(f"{reference_id}: 证据摘录与冻结转录不符")
                    if (
                        reference.get("start_ms") != segment["start_ms"]
                        or reference.get("end_ms") != segment["end_ms"]
                    ):
                        raise EvaluationInputError(f"{reference_id}: 证据时间范围与冻结转录不符")
                    referenced_ids.add(segment_id)
                if referenced_ids != set(evidence):
                    raise EvaluationInputError(
                        f"{reference_id}: evidence_references 与 evidence_segment_ids 不一致"
                    )
        annotator = annotation.get("annotator") or {}
        if qualification == "human_verified":
            if not isinstance(annotation.get("annotation_complete"), bool):
                raise EvaluationInputError(
                    f"{case_id}: human_verified 必须声明 annotation_complete 布尔值"
                )
            if not annotator.get("name") or not annotator.get("reviewed_at"):
                raise EvaluationInputError(f"{case_id}: human_verified 缺真实审核人或时间")
            if verification.get("transcript_support") != "verified":
                raise EvaluationInputError(f"{case_id}: human_verified 必须完成冻结文字核验")
            required_statuses = {"speaker", "stance", "horizon", "topic", "entity", "condition"}
            for viewpoint in viewpoints:
                if not required_statuses.issubset(viewpoint["field_status"]):
                    raise EvaluationInputError(
                        f"{viewpoint['reference_id']}: human_verified 缺完整 field_status"
                    )
                field_status = viewpoint["field_status"]
                field_presence = {
                    "speaker": bool(str(viewpoint.get("speaker") or "").strip()),
                    "stance": viewpoint.get("stance") != "unclear",
                    "horizon": viewpoint.get("horizon") is not None,
                    "topic": bool(str(viewpoint.get("topic") or "").strip()),
                    "entity": bool(viewpoint.get("entities")),
                    "condition": viewpoint.get("conditional") is True
                    and bool(str(viewpoint.get("condition_text") or "").strip()),
                }
                for field, present in field_presence.items():
                    if (field_status[field] == "present") != present:
                        raise EvaluationInputError(
                            f"{viewpoint['reference_id']}: {field} 与 field_status 不一致"
                        )

        annotation_hash = _sha256_file(annotation_path)
        annotation_hashes[case_id] = annotation_hash
        transcript_hashes[case_id] = transcript_hash
        cases[case_id] = {
            "source": source,
            "transcript": transcript,
            "segment_ids": segment_ids,
            "segment_map": segment_map,
            "annotation": annotation,
            "annotation_hash": annotation_hash,
            "transcript_hash": transcript_hash,
        }

    split_by_creator: dict[str, set[str]] = {}
    split_by_url: dict[str, set[str]] = {}
    for context in cases.values():
        source = context["source"]
        split_by_creator.setdefault(str(source["creator"]).strip().casefold(), set()).add(
            source["split"]
        )
        split_by_url.setdefault(str(source["url"]).strip().casefold(), set()).add(source["split"])
    leaked_creators = sorted(key for key, splits in split_by_creator.items() if len(splits) > 1)
    leaked_urls = sorted(key for key, splits in split_by_url.items() if len(splits) > 1)
    if leaked_creators or leaked_urls:
        raise EvaluationInputError(
            "development/acceptance 来源未隔离: "
            f"creators={leaked_creators}, urls={leaked_urls}"
        )

    dataset_hash = _json_hash(
        {
            "manifest": manifest,
            "transcripts": transcript_hashes,
            "annotations": annotation_hashes,
        }
    )
    return {
        "manifest": manifest,
        "dataset_version": dataset_version,
        "dataset_hash": dataset_hash,
        "annotation_hash": _json_hash(annotation_hashes),
        "cases": cases,
    }


def _candidate_list(data: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(data, dict):
        return [], ["响应顶层必须是对象"]
    if "viewpoints" not in data:
        return [], ["响应缺 viewpoints"]
    candidates = data["viewpoints"]
    if not isinstance(candidates, list):
        return [], ["viewpoints 必须是数组"]
    invalid_indexes = [index for index, item in enumerate(candidates) if not isinstance(item, dict)]
    if invalid_indexes:
        return [], [f"viewpoints 含非对象元素: {invalid_indexes}"]
    return candidates, []


def _validate_generated_candidate(candidate: dict[str, Any], allowed_ids: set[int]) -> str | None:
    if not str(candidate.get("claim") or "").strip():
        return "claim 为空"
    if candidate.get("stance") not in VALID_STANCES:
        return f"stance 非法: {candidate.get('stance')!r}"
    horizon = candidate.get("horizon")
    if horizon is not None and horizon not in VALID_HORIZONS:
        return f"horizon 非法: {horizon!r}"
    confidence = candidate.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return f"confidence 非法: {confidence!r}"
    evidence = candidate.get("evidence_segment_ids")
    if not isinstance(evidence, list) or not evidence:
        return "证据为空"
    if not all(isinstance(value, int) for value in evidence):
        return "证据 ID 非整数"
    if not set(evidence).issubset(allowed_ids):
        return "证据越界"
    return None


def _generate_predictions(dataset: Path, bundle: dict[str, Any], output: Path | None) -> Path:
    sys.path.insert(0, str(REPO / "apps/api"))
    from app.core.settings import get_settings
    from app.domain.transcript.chunker import chunk_transcript
    from app.llm.provider import LLMResponseParseError, OpenAICompatProvider
    from app.llm.registry import get_prompt_registry

    settings = get_settings()
    if not settings.llm_base_url or not settings.llm_api_key:
        raise EvaluationInputError(
            "--generate-predictions 需要环境变量 LLM_BASE_URL 与 LLM_API_KEY；离线评分不需要"
        )
    provider = OpenAICompatProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_sec=settings.llm_timeout_sec,
        max_retries=0,
        max_tokens=settings.llm_max_tokens,
    )
    registry = get_prompt_registry()
    full_pack = registry.get("extraction@v2")
    chunk_pack = registry.get("extraction@v1")
    run_id = f"eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    output_path = output or dataset / "runs" / run_id / "predictions.json"
    if output_path.exists():
        raise EvaluationInputError(f"预测输出已存在，拒绝覆盖: {output_path}")

    case_outputs: list[dict[str, Any]] = []
    execution_plan: dict[str, list[tuple[dict[str, Any], str, set[int], Any]]] = {}
    prompt_hashes: dict[str, str] = {}

    for case_id, context in bundle["cases"].items():
        source = context["source"]
        transcript = context["transcript"]
        total_chars = sum(len(str(segment["text"])) for segment in transcript)
        if total_chars <= 20_000:
            full_text = "\n".join(f"[seg:{s['id']}] {s['text']}" for s in transcript)
            prompt = (
                full_pack.user_template.replace("__SEG_COUNT__", str(len(transcript)))
                .replace("__FIRST_SEGMENT_ID__", str(transcript[0]["id"]))
                .replace("__FULL_TEXT__", full_text)
                .replace("__TITLE__", str(source.get("title") or "")[:80])
                .replace("__CREATOR__", str(source.get("creator") or ""))
            )
            units = [("whole-000", prompt, set(context["segment_ids"]), full_pack)]
            processing_mode = "whole_doc"
        else:
            chunks = chunk_transcript(transcript)
            units = [
                (
                    chunk.chunk_id,
                    chunk_pack.user_template.replace(
                        "__FIRST_SEGMENT_ID__", str(chunk.segment_ids[0])
                    ).replace("__CHUNK_TEXT__", chunk.text),
                    set(chunk.segment_ids),
                    chunk_pack,
                )
                for chunk in chunks
            ]
            processing_mode = "chunked"
        logical_units: list[dict[str, Any]] = []
        planned_units = []
        for unit_id, user_prompt, allowed_ids, pack in units:
            prompt_hashes[pack.version] = _json_hash(
                {"system": pack.system, "user_template": pack.user_template, "schema": pack.schema}
            )
            unit_record: dict[str, Any] = {
                "unit_id": unit_id,
                "prompt_version": pack.version,
                "segment_count": len(allowed_ids),
                "segment_ids": sorted(allowed_ids),
                "segment_ids_sha256": _json_hash(sorted(allowed_ids)),
                "user_prompt_sha256": _sha256_bytes(user_prompt.encode()),
                "attempts": [],
                "selected_attempt": None,
            }
            logical_units.append(unit_record)
            planned_units.append((unit_record, user_prompt, allowed_ids, pack))
        execution_plan[case_id] = planned_units
        case_outputs.append(
            {
                "case_id": case_id,
                "input_sha256": context["transcript_hash"],
                "status": "planned",
                "processing_mode": processing_mode,
                "logical_units": logical_units,
                "predictions": [],
            }
        )

    payload = {
        "schema_version": "1.0",
        "scoring_version": SCORING_VERSION,
        "run_id": run_id,
        "dataset_version": bundle["dataset_version"],
        "created_at": datetime.now(UTC).isoformat(),
        "generator": {
            "kind": "evaluation_llm",
            "model": settings.llm_model,
            "prompt_versions": prompt_hashes,
            "max_attempts": 3,
            "note": "环境变量配置；不读取设置页数据库，不写业务库",
        },
        "cases": case_outputs,
    }
    _write_json(output_path, payload)

    for case_output in case_outputs:
        case_id = case_output["case_id"]
        case_output["status"] = "running"
        _write_json(output_path, payload)
        case_status = "success"
        predictions: list[dict[str, Any]] = case_output["predictions"]
        for unit_record, user_prompt, allowed_ids, pack in execution_plan[case_id]:
            selected_candidates: list[dict[str, Any]] = []
            for attempt_no in range(1, 4):
                try:
                    response = provider.generate_json(
                        pack.system, user_prompt, pack.schema, temperature=0.2
                    )
                    candidates, response_errors = _candidate_list(response.data)
                    candidate_errors = [
                        error
                        for candidate in candidates
                        if (error := _validate_generated_candidate(candidate, allowed_ids))
                    ]
                    errors = response_errors + candidate_errors
                    schema_valid = not errors
                    unit_record["attempts"].append(
                        {
                            "attempt_no": attempt_no,
                            "status": "success" if schema_valid else "structure_failed",
                            "schema_valid": schema_valid,
                            "errors": errors,
                            "provider": response.provider,
                            "model": response.model,
                            "usage": response.usage,
                            "raw_response": response.raw_content,
                            "parsed_response": response.data,
                        }
                    )
                    _write_json(output_path, payload)
                    if schema_valid:
                        unit_record["selected_attempt"] = attempt_no
                        selected_candidates = candidates
                        break
                except LLMResponseParseError as exc:
                    unit_record["attempts"].append(
                        {
                            "attempt_no": attempt_no,
                            "status": "structure_failed",
                            "schema_valid": False,
                            "errors": [str(exc)[:500]],
                            "provider": exc.provider,
                            "model": exc.model,
                            "raw_response": exc.raw_content,
                            "parsed_response": None,
                        }
                    )
                    _write_json(output_path, payload)
                except Exception as exc:  # noqa: BLE001 - 每次尝试必须落账
                    unit_record["attempts"].append(
                        {
                            "attempt_no": attempt_no,
                            "status": "call_failed",
                            "schema_valid": False,
                            "error": str(exc)[:500],
                        }
                    )
                    _write_json(output_path, payload)
            if unit_record["selected_attempt"] is None:
                case_status = "failed"
            else:
                for index, candidate in enumerate(selected_candidates, start=1):
                    predictions.append(
                        {
                            "prediction_id": (
                                f"{case_id}-{unit_record['unit_id']}-pred-{index:03d}"
                            ),
                            **candidate,
                        }
                    )
            _write_json(output_path, payload)
        case_output["status"] = case_status
        _write_json(output_path, payload)

    payload["completed_at"] = datetime.now(UTC).isoformat()
    _write_json(output_path, payload)
    return output_path


def _load_predictions(path: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise EvaluationInputError("预测文件必须是含 cases 数组的对象")
    if payload.get("dataset_version") != bundle["dataset_version"]:
        raise EvaluationInputError("预测文件 dataset_version 与冻结数据不一致")
    if payload.get("scoring_version") != SCORING_VERSION:
        raise EvaluationInputError("预测文件 scoring_version 不受支持")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise EvaluationInputError("预测文件缺 run_id")
    formal_case_ids = {
        case_id
        for case_id, context in bundle["cases"].items()
        if context["source"]["split"] == "acceptance"
        and context["annotation"]["qualification"] == "human_verified"
        and context["annotation"].get("annotation_complete") is True
    }
    generator = payload.get("generator")
    if formal_case_ids:
        if not isinstance(generator, dict) or generator.get("kind") != "evaluation_llm":
            raise EvaluationInputError("正式预测缺 evaluation_llm 生成来源")
        if not str(generator.get("model") or "").strip():
            raise EvaluationInputError("正式预测缺实际 model")
        if not isinstance(generator.get("prompt_versions"), dict):
            raise EvaluationInputError("正式预测缺提示词版本与哈希")
    generator_info: dict[str, Any] = generator if isinstance(generator, dict) else {}

    seen_cases: set[str] = set()
    prediction_ids: set[str] = set()
    cases: dict[str, dict[str, Any]] = {}
    for result in payload["cases"]:
        if not isinstance(result, dict):
            raise EvaluationInputError("预测 cases 元素必须是对象")
        case_id = str(result.get("case_id") or "").strip()
        if case_id not in bundle["cases"] or case_id in seen_cases:
            raise EvaluationInputError(f"预测 case_id 未知或重复: {case_id!r}")
        seen_cases.add(case_id)
        context = bundle["cases"][case_id]
        if result.get("input_sha256") != context["transcript_hash"]:
            raise EvaluationInputError(f"{case_id}: 预测绑定的转录哈希已过期")
        result_status = result.get("status")
        if result_status not in {"planned", "running", "success", "partial", "failed"}:
            raise EvaluationInputError(f"{case_id}: status 非法")
        units = result.get("logical_units")
        if not isinstance(units, list) or not units:
            raise EvaluationInputError(f"{case_id}: logical_units 为空")
        unit_ids: set[str] = set()
        covered_segment_ids: set[int] = set()
        formal_eligible = case_id in formal_case_ids
        expected_plan: dict[str, Any] = (
            _expected_input_plan(context) if formal_eligible else {}
        )
        if formal_eligible and result.get("processing_mode") != expected_plan["processing_mode"]:
            raise EvaluationInputError(f"{case_id}: processing_mode 与当前确定性规则不一致")
        if formal_eligible and len(units) != len(expected_plan["units"]):
            raise EvaluationInputError(f"{case_id}: 逻辑单元数量与当前确定性规则不一致")
        selected_candidate_snapshots: list[str] = []
        for unit in units:
            unit_id = str(unit.get("unit_id") or "").strip() if isinstance(unit, dict) else ""
            if not unit_id or unit_id in unit_ids:
                raise EvaluationInputError(f"{case_id}: logical unit 缺失或重复")
            unit_ids.add(unit_id)
            if formal_eligible:
                expected_unit = expected_plan["units"][len(unit_ids) - 1]
                if unit_id != expected_unit["unit_id"]:
                    raise EvaluationInputError(f"{case_id}/{unit_id}: unit_id 与计划不一致")
                if unit.get("prompt_version") != expected_unit["prompt_version"]:
                    raise EvaluationInputError(f"{case_id}/{unit_id}: prompt_version 不一致")
                if unit.get("user_prompt_sha256") != expected_unit["user_prompt_sha256"]:
                    raise EvaluationInputError(f"{case_id}/{unit_id}: user prompt 哈希不一致")
                prompt_versions = generator_info["prompt_versions"]
                if (
                    prompt_versions.get(expected_unit["prompt_version"])
                    != expected_unit["prompt_sha256"]
                ):
                    raise EvaluationInputError(f"{case_id}/{unit_id}: prompt 内容哈希不一致")
            unit_segment_ids = unit.get("segment_ids")
            if unit_segment_ids is not None:
                if not isinstance(unit_segment_ids, list) or not all(
                    isinstance(value, int) for value in unit_segment_ids
                ):
                    raise EvaluationInputError(f"{case_id}/{unit_id}: segment_ids 非法")
                if not set(unit_segment_ids).issubset(context["segment_ids"]):
                    raise EvaluationInputError(f"{case_id}/{unit_id}: segment_ids 越界")
                if unit.get("segment_count") != len(unit_segment_ids):
                    raise EvaluationInputError(f"{case_id}/{unit_id}: segment_count 不一致")
                if unit.get("segment_ids_sha256") != _json_hash(sorted(unit_segment_ids)):
                    raise EvaluationInputError(f"{case_id}/{unit_id}: segment_ids 哈希不一致")
                if formal_eligible and unit_segment_ids != expected_unit["segment_ids"]:
                    raise EvaluationInputError(f"{case_id}/{unit_id}: segment_ids 与计划不一致")
                covered_segment_ids.update(unit_segment_ids)
            elif formal_eligible:
                raise EvaluationInputError(f"{case_id}/{unit_id}: 正式运行缺 segment_ids 计划")
            attempts = unit.get("attempts")
            minimum_attempts = 0 if result_status in {"planned", "running"} else 1
            if not isinstance(attempts, list) or not minimum_attempts <= len(attempts) <= 3:
                raise EvaluationInputError(
                    f"{case_id}/{unit_id}: attempts 必须为 {minimum_attempts}–3 次"
                )
            expected_numbers = list(range(1, len(attempts) + 1))
            if any(not isinstance(attempt, dict) for attempt in attempts) or [
                attempt.get("attempt_no") for attempt in attempts
            ] != expected_numbers:
                raise EvaluationInputError(f"{case_id}/{unit_id}: attempt_no 不连续")
            valid_attempt_numbers = []
            if formal_eligible:
                for attempt in attempts:
                    status = attempt.get("status")
                    schema_valid = attempt.get("schema_valid")
                    if status not in {"success", "structure_failed", "call_failed"}:
                        raise EvaluationInputError(f"{case_id}/{unit_id}: attempt status 非法")
                    if not isinstance(schema_valid, bool):
                        raise EvaluationInputError(f"{case_id}/{unit_id}: schema_valid 非布尔值")
                    if status == "call_failed":
                        if schema_valid:
                            raise EvaluationInputError(
                                f"{case_id}/{unit_id}: call_failed 不得 schema_valid"
                            )
                        continue
                    if not str(attempt.get("provider") or "").strip():
                        raise EvaluationInputError(
                            f"{case_id}/{unit_id}: 模型响应尝试缺 provider"
                        )
                    if attempt.get("model") != generator_info.get("model"):
                        raise EvaluationInputError(
                            f"{case_id}/{unit_id}: attempt model 与 generator 不一致"
                        )
                    if not isinstance(attempt.get("raw_response"), str):
                        raise EvaluationInputError(
                            f"{case_id}/{unit_id}: 模型响应尝试缺完整 raw_response"
                        )
                    raw_parse_failed = False
                    try:
                        reparsed_response = _parse_model_response(attempt["raw_response"])
                    except (json.JSONDecodeError, ValueError):
                        raw_parse_failed = True
                        reparsed_response = None
                    parsed_response = attempt.get("parsed_response")
                    if reparsed_response != parsed_response:
                        raise EvaluationInputError(
                            f"{case_id}/{unit_id}: raw_response 与 parsed_response 不一致"
                        )
                    candidates, response_errors = _candidate_list(parsed_response)
                    candidate_errors = [
                        error
                        for candidate in candidates
                        if (
                            error := _validate_generated_candidate(
                                candidate, set(unit_segment_ids or [])
                            )
                        )
                    ]
                    derived_valid = (
                        not raw_parse_failed and not response_errors and not candidate_errors
                    )
                    expected_status = "success" if derived_valid else "structure_failed"
                    if schema_valid != derived_valid or status != expected_status:
                        raise EvaluationInputError(
                            f"{case_id}/{unit_id}: schema_valid/status 与响应内容不一致"
                        )
                    if derived_valid:
                        valid_attempt_numbers.append(attempt["attempt_no"])
            selected = unit.get("selected_attempt")
            if selected is not None:
                matching = [a for a in attempts if a.get("attempt_no") == selected]
                if not matching or not matching[0].get("schema_valid"):
                    raise EvaluationInputError(f"{case_id}/{unit_id}: selected_attempt 无效")
                if formal_eligible:
                    selected_record = matching[0]
                    if not isinstance(selected_record.get("raw_response"), str):
                        raise EvaluationInputError(
                            f"{case_id}/{unit_id}: 正式运行缺完整 raw_response"
                        )
                    candidates, response_errors = _candidate_list(
                        selected_record.get("parsed_response")
                    )
                    candidate_errors = [
                        error
                        for candidate in candidates
                        if (
                            error := _validate_generated_candidate(
                                candidate, set(unit_segment_ids or [])
                            )
                        )
                    ]
                    if response_errors or candidate_errors:
                        raise EvaluationInputError(
                            f"{case_id}/{unit_id}: selected schema_valid 与内容不一致"
                        )
                    selected_candidate_snapshots.extend(
                        json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                        for candidate in candidates
                    )
            if formal_eligible and valid_attempt_numbers:
                if selected != min(valid_attempt_numbers):
                    raise EvaluationInputError(
                        f"{case_id}/{unit_id}: 未选择首次有效尝试"
                    )
                if selected != attempts[-1]["attempt_no"]:
                    raise EvaluationInputError(f"{case_id}/{unit_id}: 有效尝试后仍存在多余重试")
        if formal_eligible and covered_segment_ids != context["segment_ids"]:
            missing = sorted(context["segment_ids"] - covered_segment_ids)
            raise EvaluationInputError(f"{case_id}: 逻辑单元未覆盖完整转录: {missing[:20]}")
        predictions = result.get("predictions")
        if not isinstance(predictions, list):
            raise EvaluationInputError(f"{case_id}: predictions 必须是数组")
        for prediction in predictions:
            if not isinstance(prediction, dict):
                raise EvaluationInputError(f"{case_id}: prediction 必须是对象")
            prediction_id = str(prediction.get("prediction_id") or "").strip()
            if not prediction_id or prediction_id in prediction_ids:
                raise EvaluationInputError(f"prediction_id 缺失或重复: {prediction_id!r}")
            prediction_ids.add(prediction_id)
            if not str(prediction.get("claim") or "").strip():
                raise EvaluationInputError(f"{prediction_id}: claim 为空")
            if prediction.get("stance") not in VALID_STANCES:
                raise EvaluationInputError(f"{prediction_id}: stance 非法")
            horizon = prediction.get("horizon")
            if horizon is not None and horizon not in VALID_HORIZONS:
                raise EvaluationInputError(f"{prediction_id}: horizon 非法")
            confidence = prediction.get("confidence")
            if confidence is not None and (
                not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1
            ):
                raise EvaluationInputError(f"{prediction_id}: confidence 非法")
            conditional = prediction.get("conditional")
            if conditional is not None and not isinstance(conditional, bool):
                raise EvaluationInputError(f"{prediction_id}: conditional 必须是布尔值")
            entities = prediction.get("entities")
            if entities is not None and (
                not isinstance(entities, list)
                or any(
                    not isinstance(entity, dict)
                    or not str(entity.get("raw_name") or "").strip()
                    for entity in entities
                )
            ):
                raise EvaluationInputError(f"{prediction_id}: entities 非法")
            topic = prediction.get("topic")
            if topic is not None and not isinstance(topic, str):
                raise EvaluationInputError(f"{prediction_id}: topic 非法")
            evidence = prediction.get("evidence_segment_ids")
            if not isinstance(evidence, list) or not all(
                isinstance(value, int) for value in evidence
            ):
                raise EvaluationInputError(f"{prediction_id}: evidence_segment_ids 非法")
        if formal_eligible:
            frozen_candidate_snapshots = [
                json.dumps(
                    {key: value for key, value in prediction.items() if key != "prediction_id"},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for prediction in predictions
            ]
            if sorted(frozen_candidate_snapshots) != sorted(selected_candidate_snapshots):
                raise EvaluationInputError(f"{case_id}: predictions 与选中模型响应不一致")
        cases[case_id] = result
    return {
        "payload": payload,
        "path": path,
        "prediction_hash": _sha256_file(path),
        "cases": cases,
        "prediction_ids": prediction_ids,
    }


def _load_adjudications(
    path: Path | None, bundle: dict[str, Any], predictions: dict[str, Any]
) -> dict[str, Any]:
    if path is None:
        return {
            "decisions": {},
            "stale": False,
            "path": None,
            "hash": None,
            "bindings": None,
        }
    payload = _read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        raise EvaluationInputError("裁决文件必须是含 decisions 数组的对象")
    if payload.get("run_id") != predictions["payload"].get("run_id"):
        raise EvaluationInputError("裁决 run_id 与预测不一致")
    bindings = payload.get("bindings") or {}
    stale = any(
        (
            bindings.get("dataset_sha256") != bundle["dataset_hash"],
            bindings.get("prediction_sha256") != predictions["prediction_hash"],
            bindings.get("annotations_sha256") != bundle["annotation_hash"],
        )
    )
    decisions: dict[str, dict[str, Any]] = {}
    used_references: set[str] = set()
    valid_references = {
        viewpoint["reference_id"]
        for context in bundle["cases"].values()
        for viewpoint in context["annotation"]["viewpoints"]
    }
    for decision in payload["decisions"]:
        if not isinstance(decision, dict):
            raise EvaluationInputError("裁决项必须是对象")
        prediction_id = str(decision.get("prediction_id") or "").strip()
        status = decision.get("status")
        reference_id = decision.get("reference_id")
        if prediction_id not in predictions["prediction_ids"] or prediction_id in decisions:
            raise EvaluationInputError(f"裁决 prediction_id 未知或重复: {prediction_id!r}")
        if status not in VALID_DECISIONS:
            raise EvaluationInputError(f"{prediction_id}: 裁决 status 非法")
        if reference_id is not None:
            if reference_id not in valid_references or reference_id in used_references:
                raise EvaluationInputError(f"{prediction_id}: reference_id 未知或重复绑定")
            used_references.add(reference_id)
        if status == "pass" and not reference_id:
            raise EvaluationInputError(f"{prediction_id}: pass 裁决缺 reference_id")
        if status in {"pass", "fail"} and (
            not decision.get("reviewer") or not decision.get("reviewed_at")
        ):
            raise EvaluationInputError(f"{prediction_id}: 已审裁决缺 reviewer/time")
        decisions[prediction_id] = decision
    if stale:
        for decision in decisions.values():
            if decision.get("status") in {"pass", "fail"}:
                decision["original_status"] = decision["status"]
                decision["status"] = "pending"
                decision["stale_reason"] = "冻结输入、预测或标注哈希已变化"
    return {
        "decisions": decisions,
        "stale": stale,
        "path": path,
        "hash": _sha256_file(path),
        "bindings": bindings,
    }


def _build_review_packet(
    bundle: dict[str, Any], predictions: dict[str, Any], adjudications: dict[str, Any]
) -> dict[str, Any]:
    decisions = []
    for case_id, result in predictions["cases"].items():
        context = bundle["cases"][case_id]
        source = context["source"]
        references = context["annotation"]["viewpoints"]
        for prediction in result["predictions"]:
            prediction_id = prediction["prediction_id"]
            existing = adjudications["decisions"].get(prediction_id)
            selected_reference_id = None if existing is None else existing.get("reference_id")
            candidates = sorted(
                (
                    {
                        "reference_id": reference["reference_id"],
                        "claim": reference["claim"],
                        "claim_similarity": _similar(prediction["claim"], reference["claim"]),
                        "fields": {
                            key: reference.get(key)
                            for key in (
                                "statement_type",
                                "speaker",
                                "stance",
                                "horizon",
                                "conditional",
                                "condition_text",
                                "topic",
                                "entities",
                                "field_status",
                            )
                        },
                        "evidence_segments": [
                            context["segment_map"][segment_id]
                            for segment_id in reference.get("evidence_segment_ids") or []
                            if segment_id in context["segment_map"]
                        ],
                    }
                    for reference in references
                ),
                key=lambda item: (
                    item["reference_id"] == selected_reference_id,
                    item["claim_similarity"],
                ),
                reverse=True,
            )[:3]
            evidence_segments = []
            for segment_id in prediction.get("evidence_segment_ids") or []:
                segment = context["segment_map"].get(segment_id)
                evidence_segments.append(
                    segment if segment is not None else {"id": segment_id, "missing": True}
                )
            decision = {
                "case_id": case_id,
                "prediction_id": prediction_id,
                "status": "pending",
                "reference_id": None,
                "reviewer": None,
                "reviewed_at": None,
                "reason": None,
            }
            if existing:
                decision.update(existing)
            decision.update(
                {
                    "source": {
                        key: source.get(key)
                        for key in (
                            "platform",
                            "url",
                            "creator",
                            "title",
                            "content_type",
                            "published_at",
                        )
                    },
                    "prediction": prediction,
                    "prediction_evidence_segments": evidence_segments,
                    "candidate_references": candidates,
                    "annotation_verification": context["annotation"].get("verification"),
                }
            )
            decisions.append(
                decision
            )
    return {
        "schema_version": "1.0",
        "run_id": predictions["payload"]["run_id"],
        "bindings": {
            "dataset_sha256": bundle["dataset_hash"],
            "prediction_sha256": predictions["prediction_hash"],
            "annotations_sha256": bundle["annotation_hash"],
        },
        "instructions": (
            "逐条核对冻结文字；确认一对一 reference 后填写 pass/fail、"
            "真实 reviewer 和 reviewed_at。"
        ),
        "decisions": decisions,
    }


def _score(
    bundle: dict[str, Any],
    predictions: dict[str, Any],
    adjudications: dict[str, Any],
    thresholds: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], int]:
    eligible_case_ids = {
        case_id
        for case_id, context in bundle["cases"].items()
        if context["source"]["split"] == "acceptance"
        and context["annotation"]["qualification"] == "human_verified"
        and context["annotation"].get("annotation_complete") is True
    }
    metric_case_ids = set(bundle["cases"]) if mode == "demo" else eligible_case_ids

    planned_units = first_schema_pass = 0
    metric_planned_units = metric_first_schema_pass = 0
    requested_cases = len(bundle["cases"])
    completed_cases = 0
    incomplete_cases: list[str] = []
    all_predictions: list[tuple[str, dict[str, Any]]] = []
    metric_predictions: list[tuple[str, dict[str, Any]]] = []
    evidence_missing = 0
    evidence_out_of_range = 0
    type_case_counts: dict[str, list[int]] = {}
    type_unit_counts: dict[str, list[int]] = {}

    for case_id, context in bundle["cases"].items():
        content_type = context["source"]["content_type"]
        if case_id in metric_case_ids:
            type_case_counts.setdefault(content_type, [0, 0])[1] += 1
        result = predictions["cases"].get(case_id)
        if result is None:
            incomplete_cases.append(f"{case_id}: 无预测结果")
            continue
        units = result["logical_units"]
        planned_units += len(units)
        unit_complete = True
        for unit in units:
            attempts = unit["attempts"]
            if attempts and attempts[0].get("schema_valid") is True:
                first_schema_pass += 1
                if case_id in metric_case_ids:
                    metric_first_schema_pass += 1
                    type_unit_counts.setdefault(content_type, [0, 0])[0] += 1
            if case_id in metric_case_ids:
                metric_planned_units += 1
                type_unit_counts.setdefault(content_type, [0, 0])[1] += 1
            selected = unit.get("selected_attempt")
            if selected is None:
                unit_complete = False
        if result.get("status") != "success" or not unit_complete:
            incomplete_cases.append(f"{case_id}: 最终处理不完整")
        else:
            completed_cases += 1
            if case_id in metric_case_ids:
                type_case_counts[content_type][0] += 1
        for prediction in result["predictions"]:
            all_predictions.append((case_id, prediction))
            if case_id not in metric_case_ids:
                continue
            metric_predictions.append((case_id, prediction))
            evidence = prediction.get("evidence_segment_ids") or []
            if not evidence:
                evidence_missing += 1
            elif not set(evidence).issubset(context["segment_ids"]):
                evidence_missing += 1
                evidence_out_of_range += 1

    prediction_total = len(metric_predictions)
    reviewed = semantic_pass = pending = 0
    matched_pass_references: set[str] = set()
    decision_rows: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    details: list[dict[str, Any]] = []
    references_by_id = {
        viewpoint["reference_id"]: (case_id, viewpoint)
        for case_id, context in bundle["cases"].items()
        for viewpoint in context["annotation"]["viewpoints"]
    }
    for case_id, prediction in metric_predictions:
        decision = adjudications["decisions"].get(prediction["prediction_id"])
        evidence = prediction.get("evidence_segment_ids") or []
        missing_evidence = not evidence or not set(evidence).issubset(
            bundle["cases"][case_id]["segment_ids"]
        )
        detail = {
            "case_id": case_id,
            "content_type": bundle["cases"][case_id]["source"]["content_type"],
            "prediction_id": prediction["prediction_id"],
            "adjudication": "pending" if decision is None else decision.get("status"),
            "reference_id": None if decision is None else decision.get("reference_id"),
            "semantic_supported": None,
            "missing_evidence": missing_evidence,
            "field_matches": None,
            "field_applicable": None,
        }
        if decision is None or decision.get("status") == "pending":
            pending += 1
            details.append(detail)
            continue
        reviewed += 1
        detail["semantic_supported"] = decision.get("status") == "pass"
        reference_id = decision.get("reference_id")
        if reference_id:
            reference_case, reference = references_by_id[reference_id]
            if reference_case != case_id:
                raise EvaluationInputError(
                    f"{prediction['prediction_id']}: 不能匹配其他素材的 reference"
                )
            decision_rows.append((case_id, prediction, reference, decision))
            detail["field_matches"] = {
                "stance": prediction.get("stance") == reference.get("stance"),
                "horizon": prediction.get("horizon") == reference.get("horizon"),
                "topic": str(prediction.get("topic") or "").strip().casefold()
                == str(reference.get("topic") or "").strip().casefold(),
                "entity": isinstance(prediction.get("entities"), list)
                and _normalize_entities(prediction.get("entities"))
                == _normalize_entities(reference.get("entities")),
                "conditional": prediction.get("conditional") == reference.get("conditional"),
            }
            reference_status = reference.get("field_status") or {}
            detail["field_applicable"] = {
                field: (
                    reference_status.get("condition" if field == "conditional" else field)
                    == "present"
                    and not (field == "stance" and reference.get("stance") == "unclear")
                )
                for field in ("stance", "horizon", "topic", "entity", "conditional")
            }
        if decision.get("status") == "pass":
            semantic_pass += 1
            if reference_id:
                matched_pass_references.add(reference_id)
        details.append(detail)

    field_counts = {
        "stance": [0, 0],
        "horizon": [0, 0],
        "topic": [0, 0],
        "entity": [0, 0],
        "conditional": [0, 0],
    }
    for _case_id, prediction, reference, _decision in decision_rows:
        field_status = reference.get("field_status") or {}
        comparisons = {
            "stance": prediction.get("stance") == reference.get("stance"),
            "horizon": prediction.get("horizon") == reference.get("horizon"),
            "topic": str(prediction.get("topic") or "").strip().casefold()
            == str(reference.get("topic") or "").strip().casefold(),
            "entity": isinstance(prediction.get("entities"), list)
            and _normalize_entities(prediction.get("entities"))
            == _normalize_entities(reference.get("entities")),
            "conditional": prediction.get("conditional") == reference.get("conditional"),
        }
        for field, correct in comparisons.items():
            status_key = "condition" if field == "conditional" else field
            status = field_status.get(status_key, "present")
            if status != "present" or (field == "stance" and reference.get("stance") == "unclear"):
                continue
            field_counts[field][1] += 1
            if correct:
                field_counts[field][0] += 1

    expected_references = {
        viewpoint["reference_id"]
        for case_id in metric_case_ids
        for viewpoint in bundle["cases"][case_id]["annotation"]["viewpoints"]
    }
    recall_numerator = len(expected_references & matched_pass_references)
    semantic_precision = _ratio(semantic_pass, prediction_total)
    if pending:
        semantic_precision["value"] = None

    metrics = {
        "run_coverage": _ratio(completed_cases, requested_cases),
        "first_attempt_schema_pass_rate": _ratio(
            metric_first_schema_pass, metric_planned_units
        ),
        "evidence_missing_rate": _ratio(evidence_missing, prediction_total),
        "semantic_review_coverage": _ratio(reviewed, prediction_total),
        "semantic_precision": semantic_precision,
        "semantic_precision_reviewed": _ratio(semantic_pass, reviewed),
        "viewpoint_recall": _ratio(recall_numerator, len(expected_references)),
        "stance_accuracy": _ratio(*field_counts["stance"]),
        "horizon_accuracy": _ratio(*field_counts["horizon"]),
        "topic_accuracy": _ratio(*field_counts["topic"]),
        "entity_accuracy": _ratio(*field_counts["entity"]),
        "conditional_accuracy": _ratio(*field_counts["conditional"]),
    }

    acceptance_contexts = [bundle["cases"][case_id] for case_id in eligible_case_ids]
    creators = {context["source"]["creator"] for context in acceptance_contexts}
    topics = {
        str(viewpoint.get("topic") or "").strip()
        for context in acceptance_contexts
        for viewpoint in context["annotation"]["viewpoints"]
        if str(viewpoint.get("topic") or "").strip()
    }
    content_types = {context["source"]["content_type"] for context in acceptance_contexts}
    reference_count = sum(
        len(context["annotation"]["viewpoints"]) for context in acceptance_contexts
    )
    numeric_coverage: dict[str, int] = {
        "acceptance_cases": len(acceptance_contexts),
        "reference_viewpoints": reference_count,
        "creators": len(creators),
        "topics": len(topics),
    }
    coverage: dict[str, Any] = {
        **numeric_coverage,
        "content_types": sorted(content_types),
    }

    by_content_type: dict[str, dict[str, Any]] = {}
    for content_type in sorted(VALID_CONTENT_TYPES):
        type_details = [item for item in details if item["content_type"] == content_type]
        type_case_ids = {
            case_id
            for case_id in metric_case_ids
            if bundle["cases"][case_id]["source"]["content_type"] == content_type
        }
        if not type_case_ids:
            continue
        type_reviewed = [item for item in type_details if item["adjudication"] != "pending"]
        type_passed = [item for item in type_details if item["semantic_supported"] is True]
        type_pending = len(type_details) - len(type_reviewed)
        type_expected_references = {
            viewpoint["reference_id"]
            for case_id in type_case_ids
            for viewpoint in bundle["cases"][case_id]["annotation"]["viewpoints"]
        }
        type_semantic_precision = _ratio(len(type_passed), len(type_details))
        if type_pending:
            type_semantic_precision["value"] = None
        type_field_metrics = {}
        for field in ("stance", "horizon", "topic", "entity", "conditional"):
            applicable = [
                item
                for item in type_details
                if isinstance(item["field_applicable"], dict)
                and item["field_applicable"].get(field) is True
            ]
            correct = sum(
                isinstance(item["field_matches"], dict)
                and item["field_matches"].get(field) is True
                for item in applicable
            )
            type_field_metrics[f"{field}_accuracy"] = _ratio(correct, len(applicable))
        type_units = type_unit_counts.get(content_type, [0, 0])
        type_cases = type_case_counts.get(content_type, [0, 0])
        by_content_type[content_type] = {
            "cases": len(type_case_ids),
            "metrics": {
                "run_coverage": _ratio(*type_cases),
                "first_attempt_schema_pass_rate": _ratio(*type_units),
                "evidence_missing_rate": _ratio(
                    sum(bool(item["missing_evidence"]) for item in type_details),
                    len(type_details),
                ),
                "semantic_review_coverage": _ratio(len(type_reviewed), len(type_details)),
                "semantic_precision": type_semantic_precision,
                "semantic_precision_reviewed": _ratio(len(type_passed), len(type_reviewed)),
                "viewpoint_recall": _ratio(
                    len(type_expected_references & matched_pass_references),
                    len(type_expected_references),
                ),
                **type_field_metrics,
            },
        }

    blockers: list[str] = []
    if incomplete_cases:
        blockers.extend(incomplete_cases)
    if adjudications["stale"]:
        blockers.append("人工裁决绑定已过期，需要重新审核")
    if pending:
        blockers.append(f"仍有 {pending} 条预测待人工语义裁决")
    minimum_checks = {
        "acceptance_cases": int(thresholds["minimum_acceptance_cases"]),
        "reference_viewpoints": int(thresholds["minimum_reference_viewpoints"]),
        "creators": int(thresholds["minimum_creators"]),
        "topics": int(thresholds["minimum_topics"]),
    }
    for key, minimum in minimum_checks.items():
        if numeric_coverage[key] < minimum:
            blockers.append(
                f"正式覆盖不足: {key}={numeric_coverage[key]}，至少需要 {minimum}"
            )
    missing_types = set(thresholds["required_content_types"]) - content_types
    if missing_types:
        blockers.append(f"正式覆盖缺内容类型: {', '.join(sorted(missing_types))}")

    metric_requirements = {
        "evidence_missing_rate": ("max", float(thresholds["evidence_missing_rate_max"])),
        "first_attempt_schema_pass_rate": (
            "min",
            float(thresholds["first_attempt_schema_pass_rate_min"]),
        ),
        "stance_accuracy": ("min", float(thresholds["stance_accuracy_min"])),
        "topic_accuracy": ("min", float(thresholds["topic_accuracy_min"])),
        "entity_accuracy": ("min", float(thresholds["entity_accuracy_min"])),
        "viewpoint_recall": ("min", float(thresholds["viewpoint_recall_min"])),
        "semantic_precision": ("min", float(thresholds["semantic_precision_min"])),
    }
    unavailable_metrics = []
    threshold_failures = []
    for name, (comparison, threshold) in metric_requirements.items():
        value = metrics[name]["value"]
        if value is None:
            unavailable_metrics.append(name)
        elif comparison == "min" and value < threshold:
            threshold_failures.append(f"{name}={value} < {threshold}")
        elif comparison == "max" and value > threshold:
            threshold_failures.append(f"{name}={value} > {threshold}")
    if unavailable_metrics:
        blockers.append(f"必需指标不可计算: {', '.join(unavailable_metrics)}")
    blockers.extend(f"未达门槛: {failure}" for failure in threshold_failures)

    if incomplete_cases:
        quality_status, exit_code = "failed", 1
    elif mode == "demo":
        quality_status, exit_code = "not_evaluated", 0
    elif pending or adjudications["stale"] or any(
        numeric_coverage[key] < minimum for key, minimum in minimum_checks.items()
    ) or missing_types or unavailable_metrics:
        quality_status, exit_code = "not_evaluated", 2
    elif threshold_failures:
        quality_status, exit_code = "failed", 1
    else:
        quality_status, exit_code = "passed", 0

    return (
        {
            "quality_status": quality_status,
            "exit_code": exit_code,
            "metrics": metrics,
            "coverage": coverage,
            "by_content_type": by_content_type,
            "counts": {
                "requested_cases": requested_cases,
                "completed_cases": completed_cases,
                "planned_logical_units": planned_units,
                "first_attempt_schema_pass_logical_units": first_schema_pass,
                "all_predictions": len(all_predictions),
                "metric_scope_predictions": prediction_total,
                "reviewed_predictions": reviewed,
                "pending_predictions": pending,
                "semantic_pass_predictions": semantic_pass,
                "eligible_reference_viewpoints": len(expected_references),
                "evidence_out_of_range": evidence_out_of_range,
            },
            "details": details,
            "blockers": list(dict.fromkeys(blockers)),
            "threshold_failures": threshold_failures,
        },
        exit_code,
    )


def _baseline_comparison(path: Path | None, report: dict[str, Any]) -> dict[str, Any] | None:
    if path is None:
        return None
    baseline = _read_json(path)
    same_context = (
        baseline.get("scoring_version") == report["scoring_version"]
        and baseline.get("dataset", {}).get("sha256") == report["dataset"]["sha256"]
    )
    if not same_context:
        return {
            "comparable": False,
            "reason": "baseline 的数据集或评分口径版本不同，不能直接比较",
        }
    deltas = {}
    for name, metric in report["results"]["metrics"].items():
        current = metric.get("value")
        previous = baseline.get("results", {}).get("metrics", {}).get(name, {}).get("value")
        if current is not None and previous is not None:
            deltas[name] = round(current - previous, 6)
    return {"comparable": True, "deltas": deltas}


def _render_markdown(report: dict[str, Any]) -> str:
    results = report["results"]
    lines = [
        "# 财经观点质量评测报告",
        "",
        f"- 运行：`{report['run']['run_id']}`",
        f"- 模式：`{report['mode']}`",
        f"- 质量状态：`{results['quality_status']}`",
        f"- 数据集：`{report['dataset']['version']}`",
        f"- 评分口径：`{report['scoring_version']}`",
        f"- 人工裁决哈希：`{report['adjudication']['sha256']}`",
        f"- 待审材料：`{report['review_artifact']['path']}`",
        "",
        "## 指标",
        "",
        "| 指标 | 分子 | 分母 | 值 |",
        "|---|---:|---:|---:|",
    ]
    for name, metric in results["metrics"].items():
        value = "不适用" if metric["value"] is None else str(metric["value"])
        lines.append(f"| {name} | {metric['numerator']} | {metric['denominator']} | {value} |")
    lines.extend(["", "## 正式覆盖", ""])
    for key, value in results["coverage"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 来源类型", ""])
    if results["by_content_type"]:
        for content_type, values in results["by_content_type"].items():
            lines.append(
                f"- {content_type}: cases={values['cases']}, "
                f"predictions={values['metrics']['semantic_precision']['denominator']}"
            )
    else:
        lines.append("- 无可计量来源")
    lines.extend(["", "## 逐条裁决", ""])
    if results["details"]:
        for item in results["details"]:
            lines.append(
                f"- {item['prediction_id']}: {item['adjudication']}; "
                f"reference={item['reference_id']}; missing_evidence={item['missing_evidence']}"
            )
    else:
        lines.append("- 无符合当前模式资格的预测")
    lines.extend(["", "## 阻碍原因", ""])
    if results["blockers"]:
        lines.extend(f"- {blocker}" for blocker in results["blockers"])
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            (
                "> 工具运行完成不代表线上总体准确率通过；"
                "结论只适用于本报告冻结的数据、预测和人工裁决。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_error_report(
    output_dir: Path, mode: str, category: str, reason: str
) -> tuple[Path, Path] | None:
    try:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        artifact_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
        report_path = output_dir / f"eval_error_{artifact_id}.json"
        markdown_path = output_dir / f"eval_error_{artifact_id}.md"
        report = {
            "schema_version": "1.0",
            "scoring_version": SCORING_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": mode,
            "results": {
                "quality_status": "not_evaluated",
                "exit_code": 3,
                "blockers": [f"{category}: {reason[:500]}"],
            },
        }
        _write_json(report_path, report)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            "# 财经观点质量评测错误\n\n"
            "- 质量状态：`not_evaluated`\n"
            "- 退出码：`3`\n"
            f"- 原因：{category}: {reason[:500]}\n",
            encoding="utf-8",
        )
        return report_path, markdown_path
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结财经观点的离线质量评测")
    parser.add_argument("--dataset", default="tests/golden")
    parser.add_argument("--predictions", default=None, help="冻结预测 JSON；生成模式下可作输出路径")
    parser.add_argument("--adjudications", default=None, help="人工裁决 JSON")
    parser.add_argument("--mode", choices=("demo", "formal"), default="formal")
    parser.add_argument("--generate-predictions", action="store_true")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--out", default=None, help="报告输出目录，默认 <dataset>/expected")
    args = parser.parse_args()

    dataset = (
        (REPO / args.dataset).resolve()
        if not Path(args.dataset).is_absolute()
        else Path(args.dataset)
    )
    output_dir = Path(args.out).resolve() if args.out else dataset / "expected"
    try:
        bundle = _load_dataset(dataset)
        prediction_path = Path(args.predictions).resolve() if args.predictions else None
        if args.generate_predictions:
            prediction_path = _generate_predictions(dataset, bundle, prediction_path)
        if prediction_path is None:
            raise EvaluationInputError("必须提供 --predictions，或显式使用 --generate-predictions")
        predictions = _load_predictions(prediction_path, bundle)
        adjudication_path = Path(args.adjudications).resolve() if args.adjudications else None
        adjudications = _load_adjudications(adjudication_path, bundle, predictions)
        thresholds = _read_json(dataset / "thresholds.json")
        results, exit_code = _score(bundle, predictions, adjudications, thresholds, args.mode)
        review_packet = _build_review_packet(bundle, predictions, adjudications)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        artifact_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
        report_path = output_dir / f"eval_{artifact_id}.json"
        markdown_path = output_dir / f"eval_{artifact_id}.md"
        review_path = output_dir / (
            f"review_{predictions['payload']['run_id']}_{artifact_id}.json"
        )
        _write_json(review_path, review_packet)
        report = {
            "schema_version": "1.0",
            "scoring_version": SCORING_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": args.mode,
            "dataset": {
                "path": str(dataset),
                "version": bundle["dataset_version"],
                "sha256": bundle["dataset_hash"],
                "annotations_sha256": bundle["annotation_hash"],
            },
            "run": {
                "run_id": predictions["payload"]["run_id"],
                "prediction_path": str(prediction_path),
                "prediction_sha256": predictions["prediction_hash"],
                "generator": predictions["payload"].get("generator"),
                "processing_modes": {
                    case_id: result.get("processing_mode")
                    for case_id, result in predictions["cases"].items()
                },
            },
            "adjudication": {
                "path": (
                    str(adjudications["path"]) if adjudications["path"] is not None else None
                ),
                "sha256": adjudications["hash"],
                "bindings": adjudications["bindings"],
                "stale": adjudications["stale"],
                "decision_count": len(adjudications["decisions"]),
            },
            "review_artifact": {
                "path": str(review_path),
                "sha256": _sha256_file(review_path),
                "bindings": review_packet["bindings"],
            },
            "thresholds": thresholds,
            "results": results,
        }
        baseline = Path(args.baseline).resolve() if args.baseline else None
        report["baseline_comparison"] = _baseline_comparison(baseline, report)
        _write_json(report_path, report)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(_render_markdown(report), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"报告: {report_path}")
        print(f"人工复核材料: {review_path}")
        return exit_code
    except EvaluationInputError as exc:
        paths = _write_error_report(output_dir, args.mode, "输入无效", str(exc))
        print(f"输入无效: {exc}", file=sys.stderr)
        if paths:
            print(f"错误报告: {paths[0]}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI 必须以约定退出码收口
        reason = type(exc).__name__
        paths = _write_error_report(output_dir, args.mode, "评测器错误", reason)
        print(f"评测器错误: {reason}", file=sys.stderr)
        if paths:
            print(f"错误报告: {paths[0]}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
