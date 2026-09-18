"""DouyinAdapter 单测（Task 2 Step 3）：fixture 用 dtk 5.x 实录归一化 schema。

fixture: tests/fixtures/media/douyin_post_sample.json（Task 1 spike 实录，勿手改字段名）
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from app.services.media.adapters.douyin import DouyinAdapter
from app.services.media.contracts import (
    AccountRef,
    AdapterProcessError,
    DiscoveredItem,
    DownloadResult,
    ItemRef,
    ResolvedMedia,
    UrlNotAllowedError,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "douyin_post_sample.json"
SAMPLE = json.loads(FIXTURE.read_text())

VIDEO_URL = "https://www.douyin.com/video/7686120894896327976"
NOTE_URL = "https://www.douyin.com/note/7686120894896327976"
MODAL_URL = "https://www.douyin.com/?modal_id=7686120894896327976"
SHORT_URL = "https://v.douyin.com/abc123/"

SEC_UID = "MS4wLjABAAAABjAuPf6auEmCtvGsI2TPBck_OhhcgHoeTruXjIaFYbw"


def make_adapter(sample: dict | None = None, http: httpx.Client | None = None) -> DouyinAdapter:
    client = Mock()
    client.fetch_one_video.return_value = sample if sample is not None else dict(SAMPLE)
    client.fetch_user_posts.return_value = {
        "items": [dict(SAMPLE)],
        "cursor": 1789560103000,
        "has_more": True,
    }
    if http is None:
        http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    return DouyinAdapter(
        client,
        allowlist=("douyin.com",),
        cdn_allowlist=("douyinvod.com",),
        discover_max_pages=3,
        http=http,
    )


# --- resolve ---


def test_resolve_video_url_maps_normalized_fields() -> None:
    media: ResolvedMedia = make_adapter().resolve(VIDEO_URL)
    assert media.platform == "douyin"
    assert media.external_item_id == "7686120894896327976"
    assert media.item_type == "vod"
    assert media.channel_external_id == SEC_UID
    assert media.channel_name == "新闻联播"
    assert media.channel_url == f"https://www.douyin.com/user/{SEC_UID}"
    assert media.duration_ms == 92459
    assert media.published_at == datetime(2026, 9, 16, 13, 14, 47, tzinfo=UTC)
    assert media.canonical_url == VIDEO_URL


def test_resolve_note_and_modal_id_urls_extract_id() -> None:
    assert make_adapter().resolve(NOTE_URL).external_item_id == "7686120894896327976"
    assert make_adapter().resolve(MODAL_URL).external_item_id == "7686120894896327976"


def test_resolve_short_link_follows_redirect_then_resolves() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "v.douyin.com":
            return httpx.Response(302, headers={"Location": VIDEO_URL})
        return httpx.Response(200)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    media = make_adapter(http=http).resolve(SHORT_URL)
    assert media.external_item_id == "7686120894896327976"



def test_resolve_rejects_non_douyin_host() -> None:
    with pytest.raises(UrlNotAllowedError):
        make_adapter().resolve("https://evil.example.com/video/123")


def test_resolve_short_link_target_must_pass_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "v.douyin.com":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta"})
        return httpx.Response(200)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(UrlNotAllowedError):
        make_adapter(http=http).resolve(SHORT_URL)



def test_resolve_chaos_missing_content_id_raises_process_error() -> None:
    broken = dict(SAMPLE)
    broken.pop("content_id")
    with pytest.raises(AdapterProcessError):
        make_adapter(broken).resolve(VIDEO_URL)


def test_resolve_chaos_missing_author_raises_process_error() -> None:
    broken = dict(SAMPLE)
    broken.pop("author")
    with pytest.raises(AdapterProcessError):
        make_adapter(broken).resolve(VIDEO_URL)


def test_download_chaos_media_missing_raises_process_error() -> None:
    broken = dict(SAMPLE)
    broken["media"] = None
    with pytest.raises(AdapterProcessError):
        make_adapter(broken).download_media(
            ItemRef(external_item_id="7686120894896327976", canonical_url=VIDEO_URL),
            workdir=".",
        )



# --- discover ---


def test_discover_maps_items_and_paginates() -> None:
    client = Mock()
    client.fetch_user_posts.side_effect = [
        {"items": [dict(SAMPLE)], "cursor": 111, "has_more": True},
        {"items": [dict(SAMPLE)], "cursor": 222, "has_more": False},
    ]
    adapter = DouyinAdapter(
        client,
        allowlist=("douyin.com",),
        cdn_allowlist=("douyinvod.com",),
        discover_max_pages=3,
        http=httpx.Client(),
    )
    items: list[DiscoveredItem] = adapter.discover(
        AccountRef(platform="douyin", external_id=SEC_UID, url=None, config={})
    )
    assert len(items) == 2
    assert all(i.external_item_id == "7686120894896327976" for i in items)
    assert items[0].duration_ms == 92459
    assert items[0].published_at == datetime(2026, 9, 16, 13, 14, 47, tzinfo=UTC)
    assert items[0].url == VIDEO_URL
    # 第二页必须带上第一页返回的 cursor（核对项③）
    assert client.fetch_user_posts.call_args_list[1].kwargs["max_cursor"] == 111


def test_discover_stops_at_max_pages() -> None:
    client = Mock()
    client.fetch_user_posts.return_value = {
        "items": [dict(SAMPLE)],
        "cursor": 999,
        "has_more": True,
    }
    adapter = DouyinAdapter(
        client,
        allowlist=("douyin.com",),
        cdn_allowlist=("douyinvod.com",),
        discover_max_pages=3,
        http=httpx.Client(),
    )
    items = adapter.discover(
        AccountRef(platform="douyin", external_id=SEC_UID, url=None, config={})
    )
    assert len(items) == 3
    assert client.fetch_user_posts.call_count == 3


def test_discover_parses_sec_uid_from_account_url_when_external_id_missing() -> None:
    client = Mock()
    client.fetch_user_posts.return_value = {"items": [], "cursor": 0, "has_more": False}
    adapter = DouyinAdapter(
        client,
        allowlist=("douyin.com",),
        cdn_allowlist=("douyinvod.com",),
        discover_max_pages=3,
        http=httpx.Client(),
    )
    adapter.discover(
        AccountRef(
            platform="douyin",
            external_id="",
            url=f"https://www.douyin.com/user/{SEC_UID}",
            config={},
        )
    )
    assert client.fetch_user_posts.call_args.args[0] == SEC_UID


def test_discover_chaos_missing_has_more_raises() -> None:
    client = Mock()
    client.fetch_user_posts.return_value = {"items": [dict(SAMPLE)], "cursor": 0}
    adapter = DouyinAdapter(
        client,
        allowlist=("douyin.com",),
        cdn_allowlist=("douyinvod.com",),
        discover_max_pages=3,
        http=httpx.Client(),
    )
    with pytest.raises(AdapterProcessError):
        adapter.discover(
            AccountRef(platform="douyin", external_id=SEC_UID, url=None, config={})
        )


def test_discover_without_sec_uid_raises_adapter_error() -> None:
    with pytest.raises(Exception, match="sec_uid"):
        make_adapter().discover(
            AccountRef(platform="douyin", external_id="", url=None, config={})
        )


# --- download_media ---


@pytest.fixture(autouse=True)
def _no_curl_cffi(monkeypatch: pytest.MonkeyPatch):
    """下载单测走 httpx 桩路径：屏蔽 curl_cffi（真包会发起真实网络请求）。"""
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", None)


def test_download_media_streams_to_workdir(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-mp4-bytes")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    result: DownloadResult = make_adapter(http=http).download_media(
        ItemRef(external_item_id="7686120894896327976", canonical_url=VIDEO_URL),
        workdir=tmp_path,
    )
    assert result.size_bytes == len(b"fake-mp4-bytes")
    assert Path(result.local_path).read_bytes() == b"fake-mp4-bytes"


def test_download_media_rejects_non_cdn_host(tmp_path: Path) -> None:
    # F7/SSRF：伪造上游响应携带内网直链 → 必须在下载前拒绝
    sample = json.loads(json.dumps(SAMPLE))
    sample["media"]["video"]["url"] = "http://169.254.169.254/latest/meta-data"
    with pytest.raises(UrlNotAllowedError):
        make_adapter(sample).download_media(
            ItemRef(external_item_id="7686120894896327976", canonical_url=VIDEO_URL),
            workdir=tmp_path,
        )


def test_download_media_cdn_http_error_raises_process_error(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterProcessError):
        make_adapter(http=http).download_media(
            ItemRef(external_item_id="7686120894896327976", canonical_url=VIDEO_URL),
            workdir=tmp_path,
        )


# --- fetch_subtitle ---


def test_fetch_subtitle_always_none() -> None:
    assert (
        make_adapter().fetch_subtitle(
            ItemRef(external_item_id="1", canonical_url=VIDEO_URL)
        )
        is None
    )
