"""转录分段器（RAD-040）：按目标时长在 segment 边界切 chunk，不切断单段。

规则（执行计划 §7 RAD-040）：
- 目标 5~10min（默认 target 7.5min / hard max 10min）
- 只在 segment 边界切分，超长单段独立成 chunk（绝不切开）
- chunk 间允许少量 overlap（前文携带），overlap 段通过 overlap_from 显式标记，
  viewpoint 去重必须据此知道重叠范围
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    start_ms: int
    end_ms: int
    segment_ids: list[int]
    text: str
    # 属于上一 chunk 的携带段 id（overlap）；本 chunk 的"原生"段 = segment_ids - overlap_from
    overlap_from: list[int] | None = field(default=None)


def chunk_transcript(
    segments: list[dict],
    *,
    target_ms: int = 450_000,
    max_ms: int = 600_000,
    overlap: int = 1,
) -> list[Chunk]:
    """segments: [{id, start_ms, end_ms, text}] 按 start_ms 升序。"""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: (s["start_ms"], s["end_ms"]))

    groups: list[list[dict]] = []
    current: list[dict] = []
    for seg in ordered:
        if current:
            span = seg["end_ms"] - current[0]["start_ms"]
        else:
            span = seg["end_ms"] - seg["start_ms"]
        if current and span > max_ms:
            groups.append(current)
            current = [seg]
            continue
        current.append(seg)
        if span >= target_ms:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    chunks: list[Chunk] = []
    for idx, group in enumerate(groups):
        carried: list[dict] = []
        prev: list[dict] | None = groups[idx - 1] if idx > 0 else None
        # 上一组本身已超 max（超长单段独立 chunk）→ 不作 overlap 携带，避免整段重复进上下文
        prev_oversized = prev is not None and (
            prev[-1]["end_ms"] - prev[0]["start_ms"]
        ) > max_ms
        if overlap > 0 and prev is not None and not prev_oversized:
            carried = prev[-overlap:]
            # 携带段不得与本体重复
            own_ids = {s["id"] for s in group}
            carried = [s for s in carried if s["id"] not in own_ids]
        merged = carried + group
        chunks.append(
            Chunk(
                chunk_id=f"chunk-{idx:03d}",
                start_ms=int(merged[0]["start_ms"]),
                end_ms=int(merged[-1]["end_ms"]),
                segment_ids=[s["id"] for s in merged],
                text="\n".join(s["text"] for s in merged),
                overlap_from=[s["id"] for s in carried] or None,
            )
        )
    return chunks
