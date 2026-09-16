from datetime import UTC, datetime

from app.services.media.contracts import (
    AccountRef,
    AdapterError,
    AdapterProcessError,
    AdapterTimeoutError,
    DiscoveredItem,
    DownloadResult,
    ItemRef,
    MediaSourceAdapter,
    ResolvedMedia,
    SubtitleTrack,
    UrlNotAllowedError,
)


def test_discovered_item_defaults() -> None:
    item = DiscoveredItem(
        external_item_id="v1",
        title="t",
        url="https://x/v1",
        published_at=None,
        duration_ms=None,
        metadata={},
    )
    assert item.metadata == {}
    assert item.title == "t"


def test_resolved_media_holds_subtitle_tracks_and_channel_url() -> None:
    media = ResolvedMedia(
        platform="youtube",
        external_item_id="v1",
        title="t",
        canonical_url="https://x/v1",
        thumbnail_url=None,
        duration_ms=1000,
        item_type="vod",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        channel_external_id="ch1",
        channel_name="频道",
        subtitles=(SubtitleTrack(language="zh", is_auto=False),),
        metadata={},
    )
    assert media.subtitles[0].language == "zh"
    assert media.channel_url is None  # E3：默认 None，兼容最小构造


def test_generic_adapter_satisfies_protocol() -> None:
    # 结构化 Protocol：任何实现四方法的对象都能赋给契约类型
    class _Fake:
        def discover(self, account: AccountRef) -> list[DiscoveredItem]:
            return []

        def resolve(self, url: str) -> ResolvedMedia: ...  # type: ignore[empty-body]

        def fetch_subtitle(self, item: ItemRef, language: str | None = None) -> None:
            return None

        def download_media(self, item: ItemRef) -> DownloadResult: ...  # type: ignore[empty-body]

    adapter: MediaSourceAdapter = _Fake()  # 静态满足即通过（mypy 在 CI 兜底）
    assert adapter is not None


def test_adapter_error_hierarchy() -> None:
    assert issubclass(UrlNotAllowedError, AdapterError)
    assert issubclass(AdapterProcessError, AdapterError)
    assert issubclass(AdapterTimeoutError, AdapterError)
