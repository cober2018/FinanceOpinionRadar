"""FFmpeg 音频标准化（RAD-032）单测：伪造 ffmpeg 二进制，不碰真实转码。"""

import stat
import wave
from pathlib import Path

import pytest
from app.services.media.audio import MediaAudioError, normalize_audio


def _make_wav(path: Path, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * 16000 * seconds)


def _script(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture
def make_ffmpeg(tmp_path: Path):
    """伪造 ffmpeg：把预生成的 16k mono wav 拷到 argv 中最后一个参数（输出路径）。"""

    def _make() -> str:
        wav = tmp_path / "fixture.wav"
        _make_wav(wav, seconds=1)
        return _script(
            tmp_path,
            "fake_ffmpeg",
            f'out="${{@: -1}}"\ncp "{wav}" "$out"',
        )

    return _make


@pytest.fixture
def make_bad_ffmpeg(tmp_path: Path):
    def _make() -> str:
        return _script(tmp_path, "bad_ffmpeg", 'echo "corrupt input" >&2\nexit 1')

    return _make


def test_normalize_reports_sha_and_duration(tmp_path: Path, make_ffmpeg) -> None:
    src = tmp_path / "in.m4a"
    src.write_bytes(b"fake-audio")
    out = normalize_audio(src, tmp_path, binary=make_ffmpeg(), timeout_sec=30)
    assert out.duration_ms == 1000
    assert out.path.suffix == ".wav"
    assert out.path.exists()
    assert len(out.input_sha256) == 64 and len(out.output_sha256) == 64


def test_nonzero_exit_raises_media_ffmpeg_failed(tmp_path: Path, make_bad_ffmpeg) -> None:
    (tmp_path / "in.m4a").write_bytes(b"fake-audio")
    with pytest.raises(MediaAudioError) as e:
        normalize_audio(tmp_path / "in.m4a", tmp_path, binary=make_bad_ffmpeg())
    assert e.value.code == "MEDIA_FFMPEG_FAILED"
    assert "corrupt input" in str(e.value)


def test_binary_missing_raises(tmp_path: Path) -> None:
    (tmp_path / "in.m4a").write_bytes(b"fake-audio")
    with pytest.raises(MediaAudioError, match="不存在"):
        normalize_audio(
            tmp_path / "in.m4a", tmp_path, binary="/nonexistent/ffmpeg"
        )
