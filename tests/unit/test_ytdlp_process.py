import json
from pathlib import Path

import pytest
from app.services.media.adapters.yt_dlp import YtDlpProcess
from app.services.media.contracts import AdapterProcessError, AdapterTimeoutError

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")


def run(monkeypatch: pytest.MonkeyPatch, behavior: str, payload: dict | None = None, timeout: int = 10):
    # E1：统一 monkeypatch（与 Task 6/7 同款），失败也自动还原，杜绝 env 串扰
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    if payload is not None:
        monkeypatch.setenv("FAKE_YTDLP_PAYLOAD", json.dumps(payload))
    return YtDlpProcess(binary=FAKE, timeout_sec=timeout).run_json(
        ["--dump-single-json", "https://www.youtube.com/watch?v=x"]
    )


def test_run_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch, "success", {"id": "abc"}) == {"id": "abc"}


def test_nonzero_exit_raises_with_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterProcessError, match="private"):
        run(monkeypatch, "private")


def test_unsupported_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterProcessError, match="Unsupported URL"):
        run(monkeypatch, "badurl")


def test_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterTimeoutError):
        run(monkeypatch, "timeout", timeout=1)


def test_unparseable_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterProcessError, match="无法解析"):
        run(monkeypatch, "badjson")


def test_missing_binary_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # GAP-1：二进制缺失 → AdapterProcessError 且消息含 YTDLP_BINARY 指引
    proc = YtDlpProcess(binary="/nonexistent/yt-dlp", timeout_sec=5)
    with pytest.raises(AdapterProcessError, match="YTDLP_BINARY"):
        proc.run_json(["--dump-single-json", "https://www.youtube.com/watch?v=x"])
