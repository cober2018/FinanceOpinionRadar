"""Adapter 工厂（F1）：api 与 worker 共用，二者互不依赖。

per-platform 分流（Plan #4 Task 2）：douyin → DouyinAdapter（外部 dtk 服务），
其余 → 通用 yt-dlp。detect_platform 是 host→platform 的唯一权威实现（E7：
discovery 既有代码无按 host 判平台的逻辑，无需去重，仅此一处）。
"""

from functools import lru_cache
from urllib.parse import urlsplit

from app.core.settings import get_settings
from app.services.media.adapters.douyin import DouyinAdapter
from app.services.media.adapters.douyin_client import DouyinApiClient
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import AdapterError, MediaSourceAdapter

_PLATFORM_HOST_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("douyin.com", "douyin"),
    ("youtu.be", "youtube"),
    ("youtube.com", "youtube"),
    ("bilibili.com", "bilibili"),
)

# DX R2：未配置 dtk 时的三段化错误（问题+原因+修复），文案锚点 README 抖音 Quickstart
_DOUYIN_UNCONFIGURED_MSG = (
    "问题: 环境变量 DOUYIN_API_BASE_URL 未配置，无法创建抖音适配器。"
    "原因: 抖音风控（Argus 指纹）使 yt-dlp 整体不可用，radar 不内嵌签名逻辑，"
    "抖音采集依赖外部解析服务 dtk（Evil0ctal/Douyin_TikTok_Download_API）。"
    "修复: 部署 infra/docker/docker-compose.douyin.yml 并在 .env 配置 "
    "DOUYIN_API_BASE_URL / DOUYIN_API_KEY 后重启，步骤见 README「抖音 Quickstart」。"
)


def detect_platform(url: str) -> str:
    """host 后缀匹配判定平台；未命中走 yt-dlp 通用兜底。"""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for suffix, platform in _PLATFORM_HOST_SUFFIXES:
        if host == suffix or host.endswith(f".{suffix}"):
            return platform
    return "generic"


@lru_cache
def get_media_adapter(
    platform: str | None = None,
    *,
    discover_max_pages: int | None = None,
    proxy: str | None = None,
    proxy_key: str | None = None,
) -> MediaSourceAdapter:
    s = get_settings()
    if platform == "douyin":
        if not s.douyin_api_base_url:
            raise AdapterError(_DOUYIN_UNCONFIGURED_MSG)
        return DouyinAdapter(
            DouyinApiClient(
                base_url=s.douyin_api_base_url,
                api_key=s.douyin_api_key,
                timeout_sec=s.douyin_api_timeout_sec,
            ),
            allowlist=s.media_host_allowlist,
            cdn_allowlist=s.douyin_cdn_allowlist,
            discover_max_pages=discover_max_pages
            if discover_max_pages is not None
            else s.douyin_discover_max_pages,
            proxy=proxy,
            proxy_key=proxy_key,
        )
    return GenericYtDlpAdapter(
        binary=s.ytdlp_binary,
        timeout_sec=s.ytdlp_timeout_sec,
        download_timeout_sec=s.ytdlp_download_timeout_sec,
        allowlist=s.media_host_allowlist,
        playlist_max_items=s.discover_playlist_max_items,
        cookies_file=s.ytdlp_cookies_file or None,
    )
