"""yt-dlp CLI 子进程封装与通用适配器（RAD-021，C2/ADR-0007）。"""

import json
import subprocess

from app.services.media.contracts import (
    AdapterProcessError,
    AdapterTimeoutError,
)

_STDERR_TAIL_CHARS = 500


class YtDlpProcess:
    """受控 yt-dlp 子进程：禁 shell、限时、捕获 stderr、stdout 只接受 JSON。"""

    def __init__(self, binary: str = "yt-dlp", timeout_sec: int = 60) -> None:
        self._binary = binary
        self._timeout_sec = timeout_sec

    def run_json(self, args: list[str]) -> dict:
        argv = [self._binary, *args]
        try:
            proc = subprocess.run(  # noqa: S603  argv 列表直传，无 shell 拼接
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterTimeoutError(f"yt-dlp 超时（>{self._timeout_sec}s）: {args[0]}") from exc
        except FileNotFoundError as exc:
            raise AdapterProcessError(
                f"yt-dlp 二进制不存在: {self._binary!r}，检查 YTDLP_BINARY"
            ) from exc
        if proc.returncode != 0:
            tail = proc.stderr.strip()[-_STDERR_TAIL_CHARS:]
            raise AdapterProcessError(f"yt-dlp 退出码 {proc.returncode}: {tail or '(无 stderr)'}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterProcessError(
                f"yt-dlp stdout 无法解析为 JSON: {proc.stdout[:_STDERR_TAIL_CHARS]!r}"
            ) from exc
