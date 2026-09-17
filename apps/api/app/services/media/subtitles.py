"""字幕解析（RAD-031）：json3/vtt → 毫秒段落；坏载荷返回空列表，由编排层转 ASR。"""

import json
import re
from dataclasses import dataclass

from app.services.media.contracts import SubtitleResult


@dataclass(frozen=True)
class SubtitleSegment:
    start_ms: int
    end_ms: int
    text: str


_VTT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)


def parse_subtitle(result: SubtitleResult) -> list[SubtitleSegment]:
    try:
        if result.fmt == "json3":
            return _parse_json3(result.content)
        return _parse_vtt(result.content)
    except (json.JSONDecodeError, KeyError, ValueError, IndexError):
        return []  # 坏载荷不致命：上层据此走 ASR


def is_usable(segments: list[SubtitleSegment], *, min_chars: int) -> bool:
    """清洗后总字符量门槛：低于即视为无有效字幕，编排放弃转 ASR。"""
    return sum(len(s.text) for s in segments) >= min_chars


def _parse_json3(raw: bytes) -> list[SubtitleSegment]:
    events = json.loads(raw).get("events") or []
    out: list[SubtitleSegment] = []
    for ev in events:
        segs = ev.get("segs")
        if not segs or ev.get("dDurationMs") in (None, 0, -1):
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        start = int(ev["tStartMs"])
        end = start + int(ev["dDurationMs"])
        if end > start:
            out.append(SubtitleSegment(start, end, text))
    return out


def _parse_vtt(raw: bytes) -> list[SubtitleSegment]:
    out: list[SubtitleSegment] = []
    blocks = raw.decode("utf-8", errors="replace").split("\n\n")
    for block in blocks:
        m = _VTT_TIME.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = (g[0] * 3600 + g[1] * 60 + g[2]) * 1000 + g[3]
        end = (g[4] * 3600 + g[5] * 60 + g[6]) * 1000 + g[7]
        text = " ".join(block[m.end():].split()).strip()
        if text and end > start:
            out.append(SubtitleSegment(start, end, text))
    return out
