"""fetch_subtitle（RAD-031 前半）：FAKE yt-dlp writeout 产物 → SubtitleResult / None。"""

import json
from pathlib import Path

import pytest
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import ItemRef, UrlNotAllowedError

from tests.fixtures.media.payloads import URL

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")
JSON3 = (
    '{"events":['
    '{"tStartMs":0,"dDurationMs":1500,"segs":[{"utf8":"今天"},{"utf8":"A股"}]},'
    '{"tStartMs":2000,"dDurationMs":1000,"segs":[{"utf8":"大涨"}]}]}'
)
ITEM = ItemRef(external_item_id="abc123", canonical_url=URL)


def make(
    monkeypatch: pytest.MonkeyPatch,
    *,
    behavior: str = "writeout",
    write_name: str | None = "abc123.zh-Hans.json3",
    content: str = JSON3,
) -> GenericYtDlpAdapter:
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    if write_name is not None:
        monkeypatch.setenv("FAKE_YTDLP_WRITE_NAME", write_name)
        monkeypatch.setenv("FAKE_YTDLP_CONTENT", content)
    return GenericYtDlpAdapter(binary=FAKE, timeout_sec=5, allowlist=("youtube.com",))


def test_fetch_subtitle_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    result = make(monkeypatch).fetch_subtitle(ITEM, language="zh-Hans")
    assert result is not None
    assert result.language == "zh-Hans"
    assert result.fmt == "json3"  # ENG-5A：全等断言
    assert result.content == JSON3.encode()
    assert result.auto is False


def test_fetch_subtitle_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # FAKE 走 success 行为：不写任何产物文件
    assert make(monkeypatch, behavior="success", write_name=None).fetch_subtitle(
        ITEM, language="ja"
    ) is None


def test_fetch_subtitle_auto_flag_selects_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args_file = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_YTDLP_ARGS_FILE", str(args_file))
    result = make(monkeypatch).fetch_subtitle(ITEM, language="zh-Hans", auto=True)
    assert result is not None and result.auto is True
    argv = json.loads(args_file.read_text())
    assert "--write-auto-subs" in argv
    assert "--write-subs" not in argv
    langs_at = argv.index("--sub-langs")
    assert argv[langs_at + 1] == "zh-Hans"


def test_fetch_subtitle_rejects_disallowed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = ItemRef(external_item_id="x", canonical_url="https://evil.com/watch?v=x")
    with pytest.raises(UrlNotAllowedError):
        make(monkeypatch).fetch_subtitle(bad)
