"""faster-whisper Provider 单测：假 WhisperModel 注入，不下载真模型。"""

from types import SimpleNamespace

import faster_whisper
import pytest
from app.services.transcription.contracts import TranscriptionError
from app.services.transcription.faster_whisper import FasterWhisperProvider


@pytest.fixture
def fake_model_cls(monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import MagicMock

    cls = MagicMock()
    monkeypatch.setattr(faster_whisper, "WhisperModel", cls)
    return cls


def _stub_transcribe_return():
    return (
        iter(
            [
                SimpleNamespace(start=0.0, end=1.5, text=" 今天 A股 ", avg_logprob=-0.1),
                SimpleNamespace(start=2.0, end=3.0, text="大涨", avg_logprob=-0.01),
            ]
        ),
        SimpleNamespace(language="zh", duration=3.0),
    )


def test_transcribe_maps_segments(
    fake_model_cls, tmp_path
) -> None:
    fake_model_cls.return_value.transcribe.return_value = _stub_transcribe_return()
    provider = FasterWhisperProvider(
        model_name="small", device="cpu", compute_type="int8", beam_size=5
    )
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"fake")
    r = provider.transcribe(str(wav), language="zh")
    assert r.language == "zh"
    assert r.provider == "faster-whisper"
    assert r.model == "small"
    assert r.segments[0].text == "今天 A股"
    assert r.segments[0].start_ms == 0 and r.segments[0].end_ms == 1500
    assert 0 < r.segments[0].confidence <= 1
    assert r.segments[1].text == "大涨"
    _, kwargs = fake_model_cls.return_value.transcribe.call_args
    assert kwargs["language"] == "zh" and kwargs["beam_size"] == 5


def test_model_loads_lazily(fake_model_cls, tmp_path) -> None:
    # worker 冷启动不炸：构造不触发模型加载，首次 transcribe 才 build
    provider = FasterWhisperProvider(
        model_name="small", device="cpu", compute_type="int8", beam_size=5
    )
    fake_model_cls.assert_not_called()
    fake_model_cls.return_value.transcribe.return_value = _stub_transcribe_return()
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"fake")
    provider.transcribe(str(wav))
    fake_model_cls.assert_called_once_with("small", device="cpu", compute_type="int8")


def test_drops_overrun_hallucination_segments(
    fake_model_cls, tmp_path
) -> None:
    # voice-pro 实战补丁：长静音/音乐段 Whisper 会吐时间戳无限漂移的幻觉段
    # （end 超音频时长），不截断会让迭代器永不结束——超 音频时长×ratio 即丢弃并截断
    fake_model_cls.return_value.transcribe.return_value = (
        iter(
            [
                SimpleNamespace(start=0.0, end=1.5, text="正常", avg_logprob=-0.1),
                SimpleNamespace(start=2.0, end=99.0, text="幻觉", avg_logprob=-0.1),
                SimpleNamespace(start=2.5, end=120.0, text="更幻", avg_logprob=-0.1),
            ]
        ),
        SimpleNamespace(language="zh", duration=3.0),  # max_end = 3 × 1.05 = 3.15
    )
    provider = FasterWhisperProvider(
        model_name="medium", device="cpu", compute_type="int8", beam_size=5
    )
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"fake")
    r = provider.transcribe(str(wav))
    assert [s.text for s in r.segments] == ["正常"]


def test_transcribe_uses_anti_hallucination_kwargs(fake_model_cls, tmp_path) -> None:
    # condition_on_previous_text=False：切断上一段文本对下一段的条件影响，抑制幻觉连锁
    fake_model_cls.return_value.transcribe.return_value = _stub_transcribe_return()
    provider = FasterWhisperProvider(
        model_name="medium", device="cpu", compute_type="int8", beam_size=5
    )
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"fake")
    provider.transcribe(str(wav))
    _, kwargs = fake_model_cls.return_value.transcribe.call_args
    assert kwargs["condition_on_previous_text"] is False


def test_transcribe_wraps_errors(fake_model_cls, tmp_path) -> None:
    fake_model_cls.return_value.transcribe.side_effect = RuntimeError("cuda oom")
    provider = FasterWhisperProvider(
        model_name="small", device="cpu", compute_type="int8", beam_size=5
    )
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"fake")
    with pytest.raises(TranscriptionError, match="转录失败"):
        provider.transcribe(str(wav))
