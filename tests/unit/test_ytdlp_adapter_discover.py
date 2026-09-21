import json
from pathlib import Path

import pytest
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import AccountRef, AdapterError, UrlNotAllowedError

from tests.fixtures.media.payloads import PLAYLIST_PAYLOAD

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")
ACCOUNT = AccountRef(
    platform="youtube",
    external_id="ch_42",
    url="https://www.youtube.com/@macro-diary/videos",
)


def make(monkeypatch: pytest.MonkeyPatch, behavior: str, payload: dict | None = None):
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    if payload is not None:
        monkeypatch.setenv("FAKE_YTDLP_PAYLOAD", json.dumps(payload, ensure_ascii=False))
    return GenericYtDlpAdapter(
        binary=FAKE, timeout_sec=5, allowlist=("youtube.com",), playlist_max_items=2
    )


def test_discover_maps_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    items = make(monkeypatch, "success", PLAYLIST_PAYLOAD).discover(ACCOUNT)
    assert [i.external_item_id for i in items] == ["v1", "v2"]
    assert items[0].duration_ms == 600000
    assert items[0].url.endswith("v1")
    assert items[0].title == "视频一"
    assert items[0].published_at is None  # flat-playlist 无日期，EPIC-03 resolve 时补


def test_discover_empty_playlist(monkeypatch: pytest.MonkeyPatch) -> None:
    items = make(monkeypatch, "success", {"entries": []}).discover(ACCOUNT)
    assert items == []


def test_discover_account_without_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterError, match="无 URL"):
        make(monkeypatch, "success", PLAYLIST_PAYLOAD).discover(
            AccountRef(platform="youtube", external_id="ch_42", url=None)
        )


def test_discover_rejects_bad_account_url(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = AccountRef(platform="youtube", external_id="x", url="https://evil.com/videos")
    with pytest.raises(UrlNotAllowedError):
        make(monkeypatch, "success", PLAYLIST_PAYLOAD).discover(bad)


def test_discover_skips_members_only_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    """【会员N/会员专属/会员专享 标题在发现层即不建条目——resolve 必失败，省重试预算。"""
    payload = {
        "entries": [
            {"id": "v1", "title": "【会员86】融资上杠杆", "_type": "url", "url": "https://www.youtube.com/watch?v=v1"},
            {"id": "v2", "title": "会员专属：实操复盘", "_type": "url", "url": "https://www.youtube.com/watch?v=v2"},
            {"id": "v3", "title": "免费视频聊会员制度", "_type": "url", "url": "https://www.youtube.com/watch?v=v3"},
            {"id": "v4", "title": "普通复盘 #股票", "_type": "url", "url": "https://www.youtube.com/watch?v=v4"},
        ]
    }
    items = make(monkeypatch, "success", payload).discover(ACCOUNT)
    assert [i.external_item_id for i in items] == ["v3", "v4"]
