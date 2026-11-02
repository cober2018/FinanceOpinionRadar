"""FFmpeg 音频标准化（RAD-032）：任意媒体 → mono 16kHz wav + sha256/duration。"""

import hashlib
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

_STDERR_TAIL = 500


class MediaAudioError(Exception):
    """音频标准化失败；code 对齐 PRD 错误码（MEDIA_FFMPEG_FAILED）。"""

    def __init__(self, message: str, *, code: str = "MEDIA_FFMPEG_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NormalizedAudio:
    path: Path
    input_sha256: str
    output_sha256: str
    duration_ms: int


def normalize_audio(
    input_path: Path,
    output_dir: Path,
    *,
    binary: str = "ffmpeg",
    timeout_sec: int = 600,
) -> NormalizedAudio:
    out = output_dir / f"{input_path.stem}.16k-mono.wav"
    argv = [
        binary,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(out),
    ]
    try:
        proc = subprocess.run(  # noqa: S603  argv 列表直传，无 shell 拼接
            argv, capture_output=True, text=True, timeout=timeout_sec, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaAudioError(f"ffmpeg 超时（>{timeout_sec}s）") from exc
    except FileNotFoundError as exc:
        raise MediaAudioError(
            f"ffmpeg 二进制不存在: {binary!r}，检查 FFMPEG_BINARY"
        ) from exc
    if proc.returncode != 0 or not out.exists():
        raise MediaAudioError(
            f"ffmpeg 退出码 {proc.returncode}: {proc.stderr.strip()[-_STDERR_TAIL:]}"
        )
    # ffmpeg 默认 wav 即 pcm_s16le，stdlib wave 可读头，免引 ffprobe
    with wave.open(str(out), "rb") as w:
        duration_ms = int(w.getnframes() * 1000 / w.getframerate())
    return NormalizedAudio(
        path=out,
        input_sha256=_sha256(input_path),
        output_sha256=_sha256(out),
        duration_ms=duration_ms,
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
