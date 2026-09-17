"""faster-whisper 实现（RAD-033）：模型惰性加载；置信度 = exp(avg_logprob) 截断 [0,1]。"""

import math

from app.services.transcription.contracts import (
    TranscriptionError,
    TranscriptResult,
    TranscriptSegmentResult,
)

PROVIDER = "faster-whisper"


class FasterWhisperProvider:
    def __init__(
        self, *, model_name: str, device: str, compute_type: str, beam_size: int
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._model = None  # 惰性：首次 transcribe 才加载（下载/载显存都贵）

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(f"faster-whisper 未安装: {exc}") from exc
            self._model = WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, path: str, language: str | None = None) -> TranscriptResult:
        try:
            segments_iter, info = self._get_model().transcribe(
                path, language=language, beam_size=self._beam_size, vad_filter=True
            )
            segments = tuple(
                TranscriptSegmentResult(
                    start_ms=int(s.start * 1000),
                    end_ms=int(s.end * 1000),
                    text=s.text.strip(),
                    confidence=round(min(max(math.exp(s.avg_logprob), 0.0), 1.0), 4),
                )
                for s in segments_iter
            )
        except TranscriptionError:
            raise
        except Exception as exc:  # ctranslate2/下载/解码等一揽子
            raise TranscriptionError(f"faster-whisper 转录失败: {exc}") from exc
        return TranscriptResult(
            language=getattr(info, "language", None),
            provider=PROVIDER,
            model=self._model_name,
            segments=segments,
        )
