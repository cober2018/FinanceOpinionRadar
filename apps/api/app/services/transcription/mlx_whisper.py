"""mlx-whisper 实现（Apple Silicon Metal 加速）：子进程桥接 voice-pro 的 venv_arm64。

复用 voice-pro 沉淀的 mlx 栈（Python 3.12 + mlx-metal + mlx-whisper 0.4.x），
radar 主 venv 零新依赖。实测 whisper-medium 转录 13.4min 中文音频：
CPU faster-whisper ~56min ↔ mlx ~59s（~57x）。基准与集成决策见 README「ASR 引擎」。
"""

import json
import subprocess
import tempfile
from pathlib import Path

import structlog

from app.services.transcription.contracts import (
    TranscriptionError,
    TranscriptResult,
    TranscriptSegmentResult,
)

logger = structlog.get_logger(__name__)

PROVIDER = "mlx-whisper"


class MlxWhisperProvider:
    def __init__(
        self,
        *,
        model_name: str,
        mlx_python: str,
        mlx_worker: str,
        timeout_sec: int = 3600,
    ) -> None:
        self._model_name = model_name
        self._python = mlx_python
        self._worker = mlx_worker
        self._timeout_sec = timeout_sec

    def transcribe(self, path: str, language: str | None = None) -> TranscriptResult:
        if not (Path(self._python).exists() and Path(self._worker).exists()):
            raise TranscriptionError(
                "mlx 环境缺失（问题：未找到 venv_arm64 的 python/worker；"
                "原因：ASR_MLX_PYTHON/ASR_MLX_WORKER 指向的路径不存在；"
                "修复：按 README「ASR 引擎」小节安装或回落 ASR_PROVIDER=faster_whisper）"
            )
        with tempfile.TemporaryDirectory(prefix="asr-mlx-") as tmp:
            out_json = str(Path(tmp) / "out.json")
            try:
                proc = subprocess.run(  # noqa: S603 argv 列表直传，无 shell 拼接
                    [
                        self._python,
                        self._worker,
                        "--audio",
                        path,
                        "--model",
                        self._model_name,
                        "--lang",
                        language or "auto",
                        "--output-json",
                        out_json,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_sec,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TranscriptionError(f"mlx 转录超时（>{self._timeout_sec}s）: {path}") from exc
            if proc.returncode != 0:
                raise TranscriptionError(
                    f"mlx worker 退出码 {proc.returncode}: {(proc.stderr or '')[-300:]}"
                )
            try:
                payload = json.loads(Path(out_json).read_text())
            except (json.JSONDecodeError, OSError) as exc:
                raise TranscriptionError(f"mlx 输出不可读: {exc}") from exc

        segments = tuple(
            TranscriptSegmentResult(
                start_ms=int(s["start"] * 1000),
                end_ms=int(s["end"] * 1000),
                text=s["text"].strip(),
                confidence=None,  # mlx_whisper 不回 avg_logprob，置信度置空
            )
            for s in payload.get("segments", [])
        )
        logger.info(
            "asr_mlx_done",
            path=path,
            model=payload.get("model"),
            segments=len(segments),
            elapsed_s=payload.get("elapsed_s"),
        )
        return TranscriptResult(
            language=payload.get("language"),
            provider=PROVIDER,
            model=self._model_name,
            segments=segments,
        )
