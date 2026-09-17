import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import (
    AdapterProcessError,
    AdapterTimeoutError,
    NotSingleItemError,
    UrlNotAllowedError,
)

from tests.fixtures.media.payloads import SUCCESS_PAYLOAD, URL

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")


def make_adapter(monkeypatch: pytest.MonkeyPatch, behavior: str, payload: dict | None = None):
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    if payload is not None:
        monkeypatch.setenv("FAKE_YTDLP_PAYLOAD", json.dumps(payload, ensure_ascii=False))
    return GenericYtDlpAdapter(
        binary=FAKE,
        timeout_sec=5,
        allowlist=("youtube.com", "youtu.be"),
    )


def test_resolve_success_maps_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    media = make_adapter(monkeypatch, "success", SUCCESS_PAYLOAD).resolve(URL)
    assert media.platform == "youtube"
    assert media.external_item_id == "abc123"
    assert media.title == "美联储加息点评"
    assert media.duration_ms == 1250500
    assert media.item_type == "vod"
    assert media.published_at == datetime(2026, 3, 15, tzinfo=UTC)
    assert media.channel_external_id == "ch_42"
    assert media.channel_name == "宏观日记"
    assert media.channel_url == "https://www.youtube.com/@macro-diary"  # E3
    assert media.thumbnail_url is not None
    langs = [(t.language, t.is_auto) for t in media.subtitles]
    assert ("zh-Hans", False) in langs and ("en", True) in langs


def test_resolve_no_subtitles(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        k: v
        for k, v in SUCCESS_PAYLOAD.items()
        if k not in ("subtitles", "automatic_captions")
    }
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.subtitles == ()


def test_resolve_live_item(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {**SUCCESS_PAYLOAD, "is_live": True}
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.item_type == "live"


def test_resolve_missing_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"id": "x", "extractor_key": "Youtube", "webpage_url": URL}
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.title is None and media.duration_ms is None
    assert media.published_at is None and media.channel_external_id is None
    assert media.channel_url is None  # E3
    assert media.item_type == "vod"


def test_resolve_published_at_from_unix_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    # GAP-2：timestamp 分支（upload_date 分支已被 SUCCESS_PAYLOAD 覆盖）
    payload = {**SUCCESS_PAYLOAD, "timestamp": 1773792000}
    del payload["upload_date"]
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.published_at == datetime.fromtimestamp(1773792000, tz=UTC)


def test_resolve_rejects_url_before_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", "success")
    adapter = make_adapter(monkeypatch, "success", SUCCESS_PAYLOAD)
    with pytest.raises(UrlNotAllowedError):
        adapter.resolve("https://evil.com/watch?v=x")


def test_resolve_private_video_wraps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterProcessError, match="private"):
        make_adapter(monkeypatch, "private").resolve(URL)


def test_resolve_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", "timeout")
    adapter = GenericYtDlpAdapter(binary=FAKE, timeout_sec=1, allowlist=("youtube.com",))
    with pytest.raises(AdapterTimeoutError):
        adapter.resolve(URL)


def test_resolve_rejects_playlist_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    # 注记④：频道/播放列表页在 _parse_resolved 即拒收，不进 60s 下载路径
    payload = {
        "_type": "playlist",
        "id": "UCxxx",
        "title": "某频道",
        "entries": [{"_type": "url", "id": "e1", "url": "https://www.youtube.com/watch?v=e1"}],
    }
    with pytest.raises(NotSingleItemError):
        make_adapter(monkeypatch, "success", payload).resolve(URL)
