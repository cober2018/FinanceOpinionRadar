"""Adapter 工厂（F1）：api 与 worker 共用，二者互不依赖。"""

from functools import lru_cache

from app.core.settings import get_settings
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import MediaSourceAdapter


@lru_cache
def get_media_adapter() -> MediaSourceAdapter:
    s = get_settings()
    return GenericYtDlpAdapter(
        binary=s.ytdlp_binary,
        timeout_sec=s.ytdlp_timeout_sec,
        download_timeout_sec=s.ytdlp_download_timeout_sec,
        allowlist=s.media_host_allowlist,
        playlist_max_items=s.discover_playlist_max_items,
    )
