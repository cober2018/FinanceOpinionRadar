"""抖音 VOD 适配器（Plan #4 Task 2）：走外部 dtk 服务，radar 不内嵌签名逻辑。

字段映射按 dtk 5.x 归一化 schema（Task 1 决策记录），fixture 为 spike 实录样本。
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.services.media.adapters.douyin_client import DouyinApiClient
from app.services.media.contracts import (
    AccountRef,
    AdapterError,
    AdapterProcessError,
    DiscoveredItem,
    DownloadResult,
    ItemRef,
    ResolvedMedia,
    SubtitleResult,
)
from app.services.media.url_guard import ensure_allowed_url

# 单视频 URL 三形态：/video/{id}、/note/{id}、?modal_id={id}
_AWEME_ID_RE = re.compile(r"(?:/video/|/note/|modal_id=)(\d+)")
# 账号主页 sec_uid（MS4w 开头长串）
_SEC_UID_RE = re.compile(r"/user/(MS4w[\w-]+)")


class DouyinAdapter:
    """抖音 VOD：resolve/discover/download 走 dtk；抖音无字幕轨，fetch_subtitle 恒 None。"""

    def __init__(
        self,
        client: DouyinApiClient,
        *,
        allowlist: tuple[str, ...],
        cdn_allowlist: tuple[str, ...],
        discover_max_pages: int = 3,
        page_size: int = 20,
        http: httpx.Client | None = None,
    ) -> None:
        self._client = client
        self._allowlist = allowlist
        self._cdn_allowlist = cdn_allowlist
        self._discover_max_pages = discover_max_pages
        self._page_size = page_size
        # 短链跟随与 CDN 下载共用；测试注入 MockTransport
        self._http = http or httpx.Client(timeout=60, follow_redirects=True)

    # --- 契约实现 ---

    def resolve(self, url: str) -> ResolvedMedia:
        ensure_allowed_url(url, self._allowlist)
        aweme_id = self._extract_aweme_id(url)
        data = self._client.fetch_one_video(aweme_id)
        author = data.get("author")
        if not isinstance(author, dict) or not author.get("sec_uid"):  # F8 chaos
            raise AdapterProcessError(
                f"dtk 视频 {aweme_id} 响应缺 author.sec_uid（键: {sorted(data)}）"
            )
        if not data.get("content_id"):
            raise AdapterProcessError(f"dtk 视频 {aweme_id} 响应缺 content_id")
        return ResolvedMedia(
            platform="douyin",
            external_item_id=str(data["content_id"]),
            title=data.get("title") or data.get("description"),
            canonical_url=data.get("web_url") or url,
            thumbnail_url=None,
            duration_ms=data.get("duration_ms"),
            item_type="vod",
            published_at=_parse_created_at(data.get("created_at")),
            channel_external_id=author["sec_uid"],
            channel_name=author.get("nickname"),
            subtitles=(),
            metadata={"stats": data.get("stats") or {}},
            channel_url=author.get("web_url"),
        )

    def discover(self, account: AccountRef) -> list[DiscoveredItem]:
        sec_uid = account.external_id or self._sec_uid_from_url(account.url)
        if not sec_uid:
            raise AdapterError(
                f"账号 {account.external_id!r} 缺 sec_uid：external_id 需为主页 /user/ 路径段"
            )
        items: list[DiscoveredItem] = []
        cursor = 0
        for _ in range(self._discover_max_pages):
            data = self._client.fetch_user_posts(sec_uid, max_cursor=cursor, count=self._page_size)
            if "has_more" not in data or "items" not in data:  # F8：缺字段显式失败
                raise AdapterProcessError(f"dtk user/posts 响应缺字段: {sorted(data)}")
            items.extend(_to_discovered(i) for i in data["items"])
            if not data["has_more"]:
                break
            cursor = data["cursor"]
        return items

    def fetch_subtitle(
        self, item: ItemRef, language: str | None = None, *, auto: bool = False
    ) -> SubtitleResult | None:
        return None  # 抖音 VOD 无字幕轨（prepare 编排自动走 ASR 兜底）

    def download_media(self, item: ItemRef, workdir: str | Path) -> DownloadResult:
        data = self._client.fetch_one_video(item.external_item_id)
        media = data.get("media") or {}
        video = media.get("video") if isinstance(media, dict) else None
        url = (video or {}).get("url")
        if not url:  # F7 闸前置：先拿到直链并校验，再碰网络
            raise AdapterProcessError(
                f"dtk 视频 {item.external_item_id} 响应缺 media.video.url（内容可能已删除）"
            )
        ensure_allowed_url(url, self._cdn_allowlist)  # 伪造直链指向内网 → 此处拒绝
        target = Path(workdir) / f"{item.external_item_id}.mp4"
        try:
            with self._http.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise AdapterProcessError(f"dtk CDN HTTP {resp.status_code}: 下载失败")
                with target.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as exc:
            raise AdapterProcessError(f"dtk CDN 下载失败: {exc}") from exc
        return DownloadResult(local_path=str(target), size_bytes=target.stat().st_size)

    # --- 内部 ---

    def _extract_aweme_id(self, url: str) -> str:
        m = _AWEME_ID_RE.search(url)
        if m:
            return m.group(1)
        # 短链：跟随重定向取最终页（目标过白名单）再提取
        followed = self._http.get(url)
        final = str(followed.url)
        ensure_allowed_url(final, self._allowlist)
        m = _AWEME_ID_RE.search(final)
        if not m:
            raise AdapterError(f"无法从 URL 提取视频 id（含短链目标 {final!r}）")
        return m.group(1)

    def _sec_uid_from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        m = _SEC_UID_RE.search(url)
        return m.group(1) if m else None


def _to_discovered(data: dict) -> DiscoveredItem:
    if not data.get("content_id"):  # F8 chaos
        raise AdapterProcessError(f"dtk user/posts 条目缺 content_id: {sorted(data)}")
    return DiscoveredItem(
        external_item_id=str(data["content_id"]),
        title=data.get("title") or data.get("description"),
        url=data.get("web_url") or "",
        published_at=_parse_created_at(data.get("created_at")),
        duration_ms=data.get("duration_ms"),
        metadata={"is_top": data.get("is_top")},
    )


def _parse_created_at(raw: object) -> datetime | None:
    if isinstance(raw, str) and raw:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None
