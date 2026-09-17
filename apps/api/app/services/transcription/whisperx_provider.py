"""WhisperX 薄实现（RAD-035）：对齐词级时间戳；diarization 可选。

仅在 ENABLE_WHISPERX=true 时由工厂构造；模块体内不做顶层 import。
"""

from app.services.transcription.contracts import (
    TranscriptionError,
    TranscriptResult,
    TranscriptSegmentResult,
)

PROVIDER = "whisperx"


class WhisperXProvider:
    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        enable_diarization: bool = False,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._enable_diarization = enable_diarization

    def transcribe(self, path: str, language: str | None = None) -> TranscriptResult:
        import whisperx  # 仅 flag 开启才 import

        try:
            audio = whisperx.load_audio(path)
            model = whisperx.load_model(
                self._model_name, self._device, compute_type=self._compute_type
            )
            result = model.transcribe(audio, language=language)
            result_language = result.get("language") or language
            align_model, meta = whisperx.load_align_model(result_language, self._device)
            result = whisperx.align(
                result.get("segments") or [],
                align_model,
                meta,
                audio,
                self._device,
                return_char_alignments=False,
            )
            if self._enable_diarization:
                pipeline = whisperx.DiarizationPipeline()
                labels = pipeline(audio)  # ENG-5A：只调一次，labels 复用
                result = whisperx.assign_word_speakers(labels, result)
            segments = tuple(
                TranscriptSegmentResult(
                    start_ms=int(s.get("start", 0) * 1000),
                    end_ms=int(s.get("end", 0) * 1000),
                    text=(s.get("text") or "").strip(),
                    confidence=round(s["score"], 4) if s.get("score") is not None else None,
                    speaker=s.get("speaker"),
                )
                for s in result.get("segments") or []
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"whisperx 转录失败: {exc}") from exc
        return TranscriptResult(
            language=result_language,
            provider=PROVIDER,
            model=self._model_name,
            segments=segments,
        )
