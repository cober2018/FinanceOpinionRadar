"""EPIC-09 观点抽取质量评估 CLI（RAD-091/092）。

用法：
    .venv/bin/python scripts/evaluate_viewpoints.py --dataset tests/golden
    .venv/bin/python scripts/evaluate_viewpoints.py --dataset tests/golden --baseline out/baseline.json

对 golden 数据集逐 case 跑抽取（provider 按 .env），与人工标注对照，输出六指标
（JSON + Markdown 报告）；带 --baseline 时输出 delta 并按 thresholds.json 判定是否通过门限。

注意：当前 golden 仅 2 条样例标注（完整 20 条人工标注见 tests/golden/README.md），
指标数值在样例集上只反映链路连通性，不构成质量结论。
"""

import argparse
import difflib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps/api"))

from app.db.models import SourceItem, TranscriptSegment
from app.db.session import get_session_factory

STANCE_BASE = {
    "strong_bullish": "bullish",
    "bullish": "bullish",
    "neutral": "neutral",
    "bearish": "bearish",
    "strong_bearish": "bearish",
    "unclear": "unclear",
}


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_candidates(predicted: list[dict], expected: list[dict]):
    """贪心匹配：claim 相似度 ≥0.6 视为同一观点被抽出。返回 (matched, fp, fn)。"""
    used = set()
    matched = 0
    for exp in expected:
        best, best_ratio = None, 0.0
        for i, pred in enumerate(predicted):
            if i in used:
                continue
            r = _similar(exp["claim"], pred.get("claim", ""))
            if r > best_ratio:
                best, best_ratio = i, r
        if best is not None and best_ratio >= 0.6:
            used.add(best)
            matched += 1
    return matched, len(predicted) - matched, len(expected) - matched


def stance_agreement(predicted: list[dict], expected: list[dict]) -> tuple[int, int]:
    agreed = total = 0
    for exp in expected:
        base = STANCE_BASE.get(exp.get("stance", ""), None)
        if base is None:
            continue
        for pred in predicted:
            if STANCE_BASE.get(pred.get("stance")) == base and _similar(
                exp["claim"], pred.get("claim", "")
            ) >= 0.5:
                total += 1
                if pred.get("stance") == exp.get("stance"):
                    agreed += 1
                break
    return agreed, total


def evidence_coverage(predicted: list[dict], transcript_ids: set[int]) -> tuple[int, int]:
    """证据覆盖率：候选给出的 evidence_segment_ids 落在转录段集合内的比例。"""
    ok = total = 0
    for pred in predicted:
        evs = pred.get("evidence_segment_ids") or []
        if not evs:
            continue
        total += 1
        if set(evs).issubset(transcript_ids):
            ok += 1
    return ok, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="tests/golden")
    ap.add_argument("--baseline", default=None, help="baseline JSON（RAD-092 回归对比）")
    ap.add_argument("--out", default=None, help="报告输出目录（默认 tests/golden/expected）")
    args = ap.parse_args()

    from app.llm.registry import get_prompt_registry
    from app.services.extraction import _default_provider

    dataset = REPO / args.dataset
    sources = json.loads((dataset / "sources.json").read_text())
    registry = get_prompt_registry()
    pack = registry.get("extraction@v1")
    provider = _default_provider()
    session_factory = get_session_factory()

    totals = {
        "cases": 0,
        "predicted": 0,
        "expected": 0,
        "matched": 0,
        "false_positive": 0,
        "false_negative": 0,
        "schema_pass": 0,
        "stance_agree": 0,
        "stance_total": 0,
        "entity_agree": 0,
        "entity_total": 0,
        "evidence_ok": 0,
        "evidence_total": 0,
    }
    per_case = []

    for src in sources:
        case_id = src["case_id"]
        annotation = json.loads((dataset / src["annotation"]).read_text())
        expected = annotation["viewpoints"]
        transcript = json.loads((dataset / src["transcript"]).read_text())
        transcript_ids = {t["id"] for t in transcript}

        # golden 条目应已存在于库中（转写完成）；按 external_item_id 或 url 找
        session = session_factory()
        item = (
            session.query(SourceItem).filter(SourceItem.external_item_id.contains(case_id)).first()
        )
        predicted: list[dict] = []
        if item is not None:
            from app.domain.transcript.chunker import chunk_transcript

            segs = (
                session.query(TranscriptSegment)
                .filter(TranscriptSegment.source_item_id == item.id)
                .order_by(TranscriptSegment.sequence_no)
                .all()
            )
            seg_dicts = [
                {"id": s.id, "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                for s in segs
            ]
            for chunk in chunk_transcript(seg_dicts):
                user = pack.user_template.replace(
                    "__FIRST_SEGMENT_ID__", str(chunk.segment_ids[0])
                ).replace("__CHUNK_TEXT__", chunk.text)
                try:
                    resp = provider.generate_json(pack.system, user, pack.schema, temperature=0.2)
                    predicted.extend(resp.data.get("viewpoints", []))
                    totals["schema_pass"] += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[{case_id}] chunk 失败: {exc}", file=sys.stderr)
        else:
            # 库中无该条目（如换机器）：直接对转录全文跑一次 LLM（无 chunk 落库）
            user = pack.user_template.replace(
                "__FIRST_SEGMENT_ID__", str(transcript[0]["id"] if transcript else 0)
            ).replace("__CHUNK_TEXT__", "\n".join(t["text"] for t in transcript[:50]))
            try:
                resp = provider.generate_json(pack.system, user, pack.schema, temperature=0.2)
                predicted = resp.data.get("viewpoints", [])
                totals["schema_pass"] += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[{case_id}] LLM 失败: {exc}", file=sys.stderr)

        matched, fp, fn = match_candidates(predicted, expected)
        totals["cases"] += 1
        totals["predicted"] += len(predicted)
        totals["expected"] += len(expected)
        totals["matched"] += matched
        totals["false_positive"] += fp
        totals["false_negative"] += fn
        s_agree, s_total = stance_agreement(predicted, expected)
        totals["stance_agree"] += s_agree
        totals["stance_total"] += s_total
        e_ok, e_total = evidence_coverage(predicted, transcript_ids)
        totals["evidence_ok"] += e_ok
        totals["evidence_total"] += e_total
        # entity：predicted 的 entity raw 是否出现在标注 entities 中
        for pred in predicted:
            exp_names = {
                e.get("raw_name", "")
                for exp in expected
                for e in (exp.get("entities") or [])
            }
            if not exp_names:
                continue
            totals["entity_total"] += 1
            for ent in pred.get("entities", []):
                raw = ent.get("raw_name", "")
                if any(_similar(raw, x) >= 0.6 or x in raw for x in exp_names):
                    totals["entity_agree"] += 1
                    break
        per_case.append({"case_id": case_id, "predicted": len(predicted), "expected": len(expected)})
        session.close()

    def ratio(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    metrics = {
        "extraction_precision": ratio(totals["matched"], totals["predicted"]),
        "extraction_recall": ratio(totals["matched"], totals["expected"]),
        "stance_accuracy": ratio(totals["stance_agree"], totals["stance_total"]),
        "entity_accuracy": ratio(totals["entity_agree"], totals["entity_total"]),
        "evidence_coverage": ratio(totals["evidence_ok"], totals["evidence_total"]),
        "schema_pass_rate": ratio(totals["schema_pass"], totals["cases"]),
    }

    out_dir = Path(args.out) if args.out else dataset / "expected"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(dataset),
        "cases": totals["cases"],
        "totals": totals,
        "metrics": metrics,
    }
    (out_dir / f"eval_{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))

    md = ["# 观点抽取评估报告", "", f"- 时间：{report['generated_at']}", f"- 样例：{totals['cases']} 条", ""]
    for k, v in metrics.items():
        md.append(f"- {k}: **{v}**")
    md_lines = "\n".join(md)
    (out_dir / f"eval_{stamp}.md").write_text(md_lines + "\n")

    exit_code = 0
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())
        thresholds = json.loads((dataset / "thresholds.json").read_text())
        regressions = {
            k: (base["metrics"].get(k), metrics.get(k))
            for k in metrics
            if base["metrics"].get(k) is not None
            and metrics.get(k) is not None
            and metrics[k] < base["metrics"][k] - 1e-6
        }
        gate_fail = {k: v for k, v in metrics.items() if v is not None and v < thresholds.get(k, 0)}
        if regressions:
            print("回归退化:", json.dumps(regressions, ensure_ascii=False))
            exit_code = 1
        if gate_fail:
            print("低于门限:", json.dumps(gate_fail, ensure_ascii=False))
            exit_code = 1
        print(f"baseline 对比完成，{'PASS' if exit_code == 0 else 'FAIL'}")

    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    print(f"报告: {out_dir}/eval_{stamp}.json / .md")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
