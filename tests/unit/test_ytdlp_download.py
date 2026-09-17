"""download_media（RAD-031）：FAKE yt-dlp 写伪音频产物 → DownloadResult / 报错。"""

import json
from pathlib import Path

import pytest
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import AdapterProcessError, ItemRef

from tests.fixtures.media.payloads import URL

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")
ITEM = ItemRef(external_item_id="abc123", canonical_url=URL)
AUDIO = b"ID3fake-audio-bytes"


def make(monkeypatch: pytest.MonkeyPatch, *, behavior: str = "writeout") -> GenericYtDlpAdapter:
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    monkeypatch.setenv("FAKE_YTDLP_WRITE_NAME", "abc123.m4a")
    monkeypatch.setenv("FAKE_YTDLP_CONTENT", AUDIO.decode("latin-1"))
    return GenericYtDlpAdapter(binary=FAKE, timeout_sec=5, allowlist=("youtube.com",))


def test_download_returns_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = make(monkeypatch).download_media(ITEM, tmp_path)
    p = Path(result.local_path)
    assert p.exists() and p.parent == tmp_path
    assert result.size_bytes == p.stat().st_size == len(AUDIO)


def test_download_no_output_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # FAKE 走 success：进程成功但不落任何文件 → AdapterProcessError（非超时）
    with pytest.raises(AdapterProcessError, match="未产出"):
        make(monkeypatch, behavior="success").download_media(ITEM, tmp_path)


def test_download_skips_part_residual(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ENG-3A：.part 半成品不算产出
    (tmp_path / "leftover.m4a.part").write_bytes(b"partial")
    result = make(monkeypatch).download_media(ITEM, tmp_path)
    assert Path(result.local_path).suffix == ".m4a"


def test_download_args_use_bestaudio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args_file = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_YTDLP_ARGS_FILE", str(args_file))
    workdir = tmp_path / "work"
    workdir.mkdir()
    make(monkeypatch).download_media(ITEM, workdir)
    argv = json.loads(args_file.read_text())
    f_at = argv.index("-f")
    assert argv[f_at + 1] == "bestaudio/best"
    # voice-pro 实战：YouTube SABR-only 流默认客户端会吃 403，调用统一带 android 客户端
    assert argv[argv.index("--extractor-args") + 1] == "youtube:player_client=android"


def test_download_rejects_disallowed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.media.contracts import UrlNotAllowedError

    bad = ItemRef(external_item_id="x", canonical_url="https://evil.com/watch?v=x")
    with pytest.raises(UrlNotAllowedError):
        make(monkeypatch).download_media(bad, Path("/tmp/whatever"))
