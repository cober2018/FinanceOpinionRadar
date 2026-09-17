"""ASR Provider 契约（RAD-033）：统一段形状，worker/API 只依赖此抽象。"""

from dataclasses import dataclass
from typing import Protocol


class TranscriptionError(Exception):
    """ASR 失败统一包装（模型缺失/下载失败/推理异常）。"""


@dataclass(frozen=True)
class TranscriptSegmentResult:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker: str | None = None


@dataclass(frozen=True)
class TranscriptResult:
    language: str | None
    provider: str
    model: str
    segments: tuple[TranscriptSegmentResult, ...]


class TranscriptionProvider(Protocol):
    def transcribe(self, path: str, language: str | None = None) -> TranscriptResult: ...
