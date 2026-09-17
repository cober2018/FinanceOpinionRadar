"""Transcription 包入口：工厂按 flag 分支 + 缺库回落（V1 不依赖 whisperx 才能成功）。"""

from functools import lru_cache

import structlog

from app.core.settings import get_settings
from app.services.transcription.contracts import TranscriptionProvider
from app.services.transcription.faster_whisper import FasterWhisperProvider
from app.services.transcription.whisperx_provider import WhisperXProvider

logger = structlog.get_logger(__name__)

__all__ = [
    "FasterWhisperProvider",
    "TranscriptionProvider",
    "WhisperXProvider",
    "build_transcription_provider",
    "get_transcription_provider",
]


def build_transcription_provider(
    *,
    enable_whisperx: bool,
    model_name: str,
    device: str,
    compute_type: str,
    enable_diarization: bool,
    beam_size: int = 5,
) -> TranscriptionProvider:
    """纯工厂：显式入参，便于单测覆盖 flag 组合。"""
    fw = FasterWhisperProvider(
        model_name=model_name, device=device, compute_type=compute_type, beam_size=beam_size
    )
    if not enable_whisperx:
        return fw
    try:
        import whisperx  # noqa: F401
    except ImportError:
        logger.warning("whisperx_enabled_but_not_installed_fallback", to="faster-whisper")
        return fw
    return WhisperXProvider(
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        enable_diarization=enable_diarization,
    )


@lru_cache
def get_transcription_provider() -> TranscriptionProvider:
    s = get_settings()
    return build_transcription_provider(
        enable_whisperx=s.enable_whisperx,
        model_name=s.asr_model_name,
        device=s.asr_device,
        compute_type=s.asr_compute_type,
        enable_diarization=s.enable_diarization,
        beam_size=s.asr_beam_size,
    )
