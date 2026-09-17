from datetime import UTC, datetime

import pytest
from app.api.v1.source_items import (
    CreateSourceItemRequest,
    ResolveUrlRequest,
    _create_adapter,
    _resolve_adapter,
)
from app.main import app
from app.services.media.contracts import (
    AdapterProcessError,
    AdapterTimeoutError,
    NotSingleItemError,
    ResolvedMedia,
    SubtitleTrack,
    UrlNotAllowedError,
)
from fastapi.testclient import TestClient


class StubAdapter:
    def __init__(self, resolved=None):
        self._resolved = resolved

    def resolve(self, url: str):
        if isinstance(self._resolved, Exception):
            raise self._resolved
        return self._resolved

    def discover(self, account):  # pragma: no cover - API 层不应触发
        raise AssertionError("API 层不应触发 discover")

    def fetch_subtitle(self, item, language=None):  # pragma: no cover
        raise NotImplementedError

    def download_media(self, item):  # pragma: no cover
        raise NotImplementedError


def _resolved(**over) -> ResolvedMedia:
    base: dict = {
        "platform": "youtube",
        "external_item_id": "abc123",
        "title": "美联储加息点评",
        "canonical_url": "https://www.youtube.com/watch?v=abc123",
        "thumbnail_url": "https://t/hq.jpg",
        "duration_ms": 1250500,
        "item_type": "vod",
        "published_at": datetime(2026, 3, 15, tzinfo=UTC),
        "channel_external_id": "ch_42",
        "channel_name": "宏观日记",
        "subtitles": (SubtitleTrack("zh-Hans", False), SubtitleTrack("en", True)),
        "metadata": {},
    }
    base.update(over)
    return ResolvedMedia(**base)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch):
    def _install(adapter: StubAdapter):
        # 端点按 body URL 分流（Task 2 Step 5）→ override 两个路由依赖。
        # 注意：override 按**替身签名**解析参数，body 必须带类型标注（否则被当 query 参数 422）
        def fake_resolve(body: ResolveUrlRequest) -> StubAdapter:
            return adapter

        def fake_create(body: CreateSourceItemRequest) -> StubAdapter:
            return adapter

        app.dependency_overrides[_resolve_adapter] = fake_resolve
        app.dependency_overrides[_create_adapter] = fake_create

    yield _install
    app.dependency_overrides.pop(_resolve_adapter, None)
    app.dependency_overrides.pop(_create_adapter, None)


def test_resolve_url_returns_metadata(client, install):
    install(StubAdapter(_resolved()))
    resp = client.post(
        "/api/v1/source-items/resolve-url",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "youtube"
    assert body["external_id"] == "abc123"
    assert body["duration_ms"] == 1250500
    assert body["thumbnail_url"] == "https://t/hq.jpg"
    assert body["subtitle_languages"] == ["zh-Hans", "en"]
    assert body["channel_name"] == "宏观日记"


def test_resolve_url_invalid_body_422(client, install):
    install(StubAdapter(_resolved()))
    resp = client.post("/api/v1/source-items/resolve-url", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_resolve_url_not_allowed_400(client, install):
    install(StubAdapter(UrlNotAllowedError("主机 evil.com 不在白名单")))
    resp = client.post(
        "/api/v1/source-items/resolve-url", json={"url": "https://evil.com/x"}
    )
    assert resp.status_code == 400
    assert "白名单" in resp.json()["detail"]


def test_resolve_url_upstream_failure_502(client, install):
    install(StubAdapter(AdapterProcessError("yt-dlp 退出码 1: private")))
    resp = client.post(
        "/api/v1/source-items/resolve-url", json={"url": "https://www.youtube.com/watch?v=x"}
    )
    assert resp.status_code == 502


def test_resolve_url_timeout_504(client, install):
    # GAP-3：AdapterTimeoutError → 504
    install(StubAdapter(AdapterTimeoutError("yt-dlp 超时（>60s）")))
    resp = client.post(
        "/api/v1/source-items/resolve-url", json={"url": "https://www.youtube.com/watch?v=x"}
    )
    assert resp.status_code == 504


def test_resolve_url_playlist_returns_400(client, install):
    # ENG-2A：NotSingleItemError 与白名单错误同映射 400，不得落 502/504
    install(StubAdapter(NotSingleItemError("非单条内容 URL（频道/播放列表），请提供具体视频地址")))
    resp = client.post(
        "/api/v1/source-items/resolve-url",
        json={"url": "https://www.youtube.com/@macro-diary"},
    )
    assert resp.status_code == 400
    assert "非单条内容" in resp.json()["detail"]
