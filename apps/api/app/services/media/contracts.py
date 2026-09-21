"""媒体来源 Adapter 契约（RAD-020）。

同步 Protocol（C1，ADR-0007）：与 sync SQLAlchemy 栈一致，yt-dlp 子进程天然阻塞。
本模块只含纯数据类型与抽象，禁止 import ORM/平台 SDK（C3）。
fetch_subtitle/download 的返回结构在 EPIC-03 充实字段，此处先钉住形状。
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AccountRef:
    """discover 的入参投影：只暴露 Adapter 需要的账号字段。"""

    platform: str
    external_id: str
    url: str | None
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ItemRef:
    """subtitle/download 的入参投影。"""

    external_item_id: str
    canonical_url: str


@dataclass(frozen=True)
class DiscoveredItem:
    """discover 产出的单个内容条目（对应 source_item 的一行候选）。"""

    external_item_id: str
    title: str | None
    url: str
    published_at: datetime | None
    duration_ms: int | None
    metadata: dict


@dataclass(frozen=True)
class SubtitleTrack:
    language: str
    is_auto: bool


@dataclass(frozen=True)
class ResolvedMedia:
    """resolve 的统一输出（RAD-022 API 响应即由它映射）。"""

    platform: str
    external_item_id: str
    title: str | None
    canonical_url: str
    thumbnail_url: str | None
    duration_ms: int | None
    item_type: str  # vod | live
    published_at: datetime | None
    channel_external_id: str | None
    channel_name: str | None
    subtitles: tuple[SubtitleTrack, ...]
    metadata: dict
    channel_url: str | None = None  # E3：频道页 URL（账号 upsert 用），默认 None 兼容测试构造


@dataclass(frozen=True)
class SubtitleResult:
    """字幕抓取产物：内容字节 + 语言 + 格式（json3|vtt）+ 是否自动生成。"""

    language: str
    content: bytes
    fmt: str = "vtt"
    auto: bool = False


@dataclass(frozen=True)
class DownloadResult:
    """EPIC-03 落地真实字段，先钉形状。"""

    local_path: str
    size_bytes: int


class AdapterError(Exception):
    """Adapter 层错误基类：API/任务层按子类映射状态。"""


class UrlNotAllowedError(AdapterError):
    """URL 未通过 scheme/主机白名单（RAD-021 安全要求）。"""


class NotSingleItemError(AdapterError):
    """resolve 目标不是单条内容（频道/播放列表页）——注记④，提前失败防 60s 超时。"""


class AdapterProcessError(AdapterError):
    """外部进程失败（非零退出/输出不可解析），message 携带 stderr 尾部。"""


class MembersOnlyError(AdapterProcessError):
    """付费会员专属内容（如 YouTube 频道会员档）——永久性失败，重试无意义。

    preparation 据此打 members_only 标记并让 retry_failed_prepares 永久跳过。
    """

    code = "MEMBERS_ONLY"


class AdapterTimeoutError(AdapterError):
    """外部进程超时被杀。"""


class MediaSourceAdapter(Protocol):
    def discover(self, account: AccountRef) -> list[DiscoveredItem]: ...
    def resolve(self, url: str) -> ResolvedMedia: ...

    def fetch_subtitle(
        self, item: ItemRef, language: str | None = None, *, auto: bool = False
    ) -> SubtitleResult | None: ...

    def download_media(self, item: ItemRef, workdir: str | Path) -> DownloadResult: ...
