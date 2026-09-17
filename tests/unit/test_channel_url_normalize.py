"""频道 URL 规范化（注记③/ISSUE-003）：裸频道地址追加 /videos 页签。"""

import pytest
from app.services.media.adapters.yt_dlp import normalize_channel_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://www.youtube.com/channel/UCuAXFkgcl1yQ6_0FhPI_DAA",
            "https://www.youtube.com/channel/UCuAXFkgcl1yQ6_0FhPI_DAA/videos",
        ),
        (
            "https://www.youtube.com/@ValueInvesting/",
            "https://www.youtube.com/@ValueInvesting/videos",
        ),
        ("https://www.youtube.com/c/SomeName", "https://www.youtube.com/c/SomeName/videos"),
        ("https://www.youtube.com/user/oldname", "https://www.youtube.com/user/oldname/videos"),
    ],
)
def test_bare_channel_appends_videos(raw: str, expected: str) -> None:
    assert normalize_channel_url(raw, platform="youtube") == expected


@pytest.mark.parametrize(
    "already",
    [
        "https://www.youtube.com/@x/videos",
        "https://www.youtube.com/@x/streams",
        "https://www.youtube.com/@x/shorts",
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/playlist?list=PL123",
    ],
)
def test_tab_or_content_urls_untouched(already: str) -> None:
    assert normalize_channel_url(already, platform="youtube") == already


def test_non_youtube_passthrough() -> None:
    assert (
        normalize_channel_url("https://www.douyin.com/user/xyz", platform="douyin")
        == "https://www.douyin.com/user/xyz"
    )


def test_none_passthrough() -> None:
    assert normalize_channel_url(None, platform="youtube") is None
