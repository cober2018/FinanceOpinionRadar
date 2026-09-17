"""WhisperX flag 与 Provider 单测：sys.modules 注入/拔除假 whisperx 模块。"""

import sys
import types
from types import SimpleNamespace

import pytest
from app.services.transcription import build_transcription_provider
from app.services.transcription.faster_whisper import FasterWhisperProvider
from app.services.transcription.whisperx_provider import PROVIDER, WhisperXProvider


def _install_fake_whisperx(monkeypatch: pytest.MonkeyPatch, *, speaker: str | None):
    mod = types.ModuleType("whisperx")
    calls: list[tuple] = []

    mod.load_audio = lambda path: (calls.append(("load_audio", path)), f"audio:{path}")[1]

    def _load_model(name, device, compute_type=None):
        calls.append(("load_model", name))
        return SimpleNamespace(
            transcribe=lambda audio, language=None: {
                "language": "zh",
                "segments": [{"start": 0.0, "end": 1.2, "text": "你好"}],
            }
        )

    mod.load_model = _load_model
    mod.load_align_model = lambda language, device: (
        calls.append(("align_model", language)),
        (object(), {"meta": 1}),
    )[1]

    def _align(segments, align_model, meta, audio, device, return_char_alignments=False):
        calls.append(("align", len(segments)))
        return {
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.2,
                    "text": "你好",
                    "score": 0.9876,
                    "speaker": speaker,
                }
            ]
        }

    mod.align = _align
    if speaker is not None:
        mod.DiarizationPipeline = lambda *a, **k: (
            calls.append(("diarize_pipeline",)),
            (lambda audio: (calls.append(("diarize", audio)), "DF")[1]),
        )[1]
        mod.assign_word_speakers = lambda df, result: (
            calls.append(("assign",)),
            result,
        )[1]
    monkeypatch.setitem(sys.modules, "whisperx", mod)
    return calls


def test_flag_off_never_imports_whisperx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "whisperx", raising=False)
    p = build_transcription_provider(
        enable_whisperx=False,
        model_name="small",
        device="cpu",
        compute_type="int8",
        enable_diarization=False,
    )
    assert isinstance(p, FasterWhisperProvider)
    assert "whisperx" not in sys.modules


def test_flag_on_but_not_installed_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.modules 置 None 模拟"包未安装"：import whisperx 即抛 ImportError
    monkeypatch.setitem(sys.modules, "whisperx", None)
    p = build_transcription_provider(
        enable_whisperx=True,
        model_name="small",
        device="cpu",
        compute_type="int8",
        enable_diarization=True,
    )
    assert isinstance(p, FasterWhisperProvider)


def test_flag_on_and_installed_returns_whisperx(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_whisperx(monkeypatch, speaker=None)
    p = build_transcription_provider(
        enable_whisperx=True,
        model_name="small",
        device="cpu",
        compute_type="int8",
        enable_diarization=False,
    )
    assert isinstance(p, WhisperXProvider)


def test_whisperx_provider_aligns_and_diarizes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_whisperx(monkeypatch, speaker="SPEAKER_00")
    provider = WhisperXProvider(
        model_name="small", device="cpu", compute_type="int8", enable_diarization=True
    )
    r = provider.transcribe("/tmp/a.wav", language="zh")
    assert r.provider == PROVIDER == "whisperx"
    assert r.language == "zh" and r.model == "small"
    assert r.segments[0].text == "你好"
    assert r.segments[0].start_ms == 0 and r.segments[0].end_ms == 1200
    assert r.segments[0].confidence == pytest.approx(0.9876)
    assert r.segments[0].speaker == "SPEAKER_00"
    # ENG-5A：diarize 只调一次
    assert calls.count(("diarize", "audio:/tmp/a.wav")) == 1
    assert ("align", 1) in calls


def test_whisperx_wraps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.transcription.contracts import TranscriptionError

    mod = types.ModuleType("whisperx")

    def _boom(*a, **k):
        raise RuntimeError("align model download failed")

    mod.load_audio = _boom
    monkeypatch.setitem(sys.modules, "whisperx", mod)
    provider = WhisperXProvider(
        model_name="small", device="cpu", compute_type="int8", enable_diarization=False
    )
    with pytest.raises(TranscriptionError, match="whisperx 转录失败"):
        provider.transcribe("/tmp/a.wav")
