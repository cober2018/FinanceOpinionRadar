# Regression: ISSUE-002 — bare channel URL 的 flat-playlist 返回页签子播放列表
# （_type='playlist'，id=频道 id），曾被当作内容条目入库为 source_item。
# Found by /qa on 2026-09-17
# Report: .gstack/qa-reports/qa-report-localhost-2026-09-17.md
import json
from pathlib import Path

import pytest
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import AccountRef

from tests.fixtures.media.payloads import PLAYLIST_PAYLOAD

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")
ACCOUNT = AccountRef(
    platform="youtube",
    external_id="ch_tabs",
    url="https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",
)

# 真实负载形状（2026-09-17 实测）：裸频道 URL 顶层 entries 是 Videos/Live/Shorts 三个页签
TABS_PAYLOAD = {
    "_type": "playlist",
    "id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "title": "Rick Astley",
    "entries": [
        {
            "_type": "playlist",
            "id": "UCuAXFkgsw1L7xaCfnd5JJOw",
            "title": "Rick Astley - Videos",
            "url": None,
        },
        {
            "_type": "url",
            "id": "PXC_PYeB6F8",
            "title": "真条目",
            "url": "https://www.youtube.com/watch?v=PXC_PYeB6F8",
        },
    ],
}


def make(monkeypatch: pytest.MonkeyPatch, payload: dict) -> GenericYtDlpAdapter:
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", "success")
    monkeypatch.setenv("FAKE_YTDLP_PAYLOAD", json.dumps(payload, ensure_ascii=False))
    return GenericYtDlpAdapter(
        binary=FAKE, timeout_sec=5, allowlist=("youtube.com",), playlist_max_items=10
    )


def test_discover_skips_tab_sub_playlists(monkeypatch: pytest.MonkeyPatch) -> None:
    items = make(monkeypatch, TABS_PAYLOAD).discover(ACCOUNT)
    assert [i.external_item_id for i in items] == ["PXC_PYeB6F8"]


def test_discover_keeps_regular_flat_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    # _type 缺省或为 'url' 的常规 flat 条目不受影响
    items = make(monkeypatch, PLAYLIST_PAYLOAD).discover(ACCOUNT)
    assert [i.external_item_id for i in items] == ["v1", "v2"]
