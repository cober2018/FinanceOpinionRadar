"""mlx-whisper provider 单测：子进程成功/失败/输出解析（subprocess 打桩，不碰真模型）。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.transcription.contracts import TranscriptionError
from app.services.transcription.mlx_whisper import MlxWhisperProvider


def _provider() -> MlxWhisperProvider:
    return MlxWhisperProvider(
        model_name="mlx-community/whisper-medium",
        mlx_python="/fake/venv/bin/python3.12",
        mlx_worker="/fake/venv/mlx_worker.py",
        timeout_sec=60,
    )


def test_transcribe_parses_worker_json(monkeypatch: pytest.MonkeyPatch, tmp_path):
    calls: list[list] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        out = argv[argv.index("--output-json") + 1]
        Path(out).write_text(
            json.dumps(
                {
                    "language": "zh",
                    "model": "mlx-community/whisper-medium",
                    "elapsed_s": 5.0,
                    "segments": [
                        {"start": 0.0, "end": 2.5, "text": " 大家好 "},
                        {"start": 2.5, "end": 5.0, "text": "今天聊美股 "},
                    ],
                }
            )
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("app.services.transcription.mlx_whisper.subprocess.run", fake_run)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

    result = _provider().transcribe("/tmp/a.wav", language="zh")
    assert calls[0][calls[0].index("--lang") + 1] == "zh"
    assert result.provider == "mlx-whisper"
    assert result.language == "zh"
    assert [(s.start_ms, s.end_ms, s.text) for s in result.segments] == [
        (0, 2500, "大家好"),
        (2500, 5000, "今天聊美股"),
    ]
    assert result.segments[0].confidence is None


def test_transcribe_raises_on_missing_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    with pytest.raises(TranscriptionError, match="mlx 环境缺失"):
        _provider().transcribe("/tmp/a.wav")


def test_transcribe_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    monkeypatch.setattr(
        "app.services.transcription.mlx_whisper.subprocess.run",
        lambda argv, **kw: SimpleNamespace(returncode=1, stderr="boom"),
    )
    with pytest.raises(TranscriptionError, match="退出码 1"):
        _provider().transcribe("/tmp/a.wav")
