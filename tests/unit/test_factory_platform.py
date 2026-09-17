"""factory per-platform 分流单测（Task 2 Step 5 + E7 + F8）。

E7 结论：discovery 既有代码无按 host 判平台的逻辑（normalize_channel_url 只做
youtube 频道页签归一，platform 由调用方给定），故 detect_platform 为唯一权威实现。
"""

import pytest
from app.core.settings import get_settings
from app.services.media.adapters.douyin import DouyinAdapter
from app.services.media.adapters.douyin_client import DouyinApiClient
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import AdapterError
from app.services.media.factory import detect_platform, get_media_adapter


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.douyin.com/video/123", "douyin"),
        ("https://v.douyin.com/abc/", "douyin"),
        ("https://live.douyin.com/330698468897", "douyin"),
        ("https://www.douyin.com/user/MS4wLjABxxx", "douyin"),
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.bilibili.com/video/BV1zyeK6KEcb", "bilibili"),
        ("https://example.com/video/1", "generic"),
    ],
)
def test_detect_platform_by_host(url: str, expected: str) -> None:
    assert detect_platform(url) == expected


def test_get_media_adapter_no_arg_keeps_generic() -> None:
    # 无参调用兼容既有调用方（API/worker 均不感知平台）
    assert isinstance(get_media_adapter(), GenericYtDlpAdapter)


def test_get_media_adapter_douyin_unconfigured_raises_three_part_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOUYIN_API_BASE_URL", "")
    get_settings.cache_clear()
    get_media_adapter.cache_clear()
    try:
        with pytest.raises(AdapterError) as exc_info:
            get_media_adapter("douyin")
        msg = str(exc_info.value)
        # DX R2：三段化——问题 / 原因 / 修复
        assert "DOUYIN_API_BASE_URL" in msg
        assert "原因" in msg
        assert "修复" in msg
    finally:
        get_settings.cache_clear()
        get_media_adapter.cache_clear()


def test_get_media_adapter_douyin_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOUYIN_API_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("DOUYIN_API_KEY", "dtk_x")
    get_settings.cache_clear()
    get_media_adapter.cache_clear()
    try:
        adapter = get_media_adapter("douyin")
        assert isinstance(adapter, DouyinAdapter)
    finally:
        get_settings.cache_clear()
        get_media_adapter.cache_clear()


def test_douyin_client_requires_base_url() -> None:
    with pytest.raises(AdapterError):
        DouyinApiClient(base_url="", api_key="k")
