"""yt-dlp CLI 子进程封装与通用适配器（RAD-021，C2/ADR-0007）。"""

import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.services.media.contracts import (
    AccountRef,
    AdapterError,
    AdapterProcessError,
    AdapterTimeoutError,
    DiscoveredItem,
    DownloadResult,
    ItemRef,
    NotSingleItemError,
    ResolvedMedia,
    SubtitleResult,
    SubtitleTrack,
)
from app.services.media.url_guard import ensure_allowed_url

_STDERR_TAIL_CHARS = 500
# 存进 metadata 快照的 yt-dlp 键白名单（仅审计用途，schema 见 models/source.py docstring）
_METADATA_KEYS = ("view_count", "like_count", "language", "description")
_ENTRY_METADATA_KEYS = ("live_status", "view_count")


class YtDlpProcess:
    """受控 yt-dlp 子进程：禁 shell、限时、捕获 stderr、stdout 只接受 JSON。"""

    def __init__(self, binary: str = "yt-dlp", timeout_sec: int = 60) -> None:
        self._binary = binary
        self._timeout_sec = timeout_sec

    def run_json(self, args: list[str], *, timeout_sec: int | None = None) -> dict:
        effective = timeout_sec if timeout_sec is not None else self._timeout_sec
        argv = [self._binary, *args]
        try:
            proc = subprocess.run(  # noqa: S603  argv 列表直传，无 shell 拼接
                argv,
                capture_output=True,
                text=True,
                timeout=effective,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterTimeoutError(f"yt-dlp 超时（>{effective}s）: {args[0]}") from exc
        except FileNotFoundError as exc:
            raise AdapterProcessError(
                f"yt-dlp 二进制不存在: {self._binary!r}，检查 YTDLP_BINARY"
            ) from exc
        if proc.returncode != 0:
            tail = proc.stderr.strip()[-_STDERR_TAIL_CHARS:]
            raise AdapterProcessError(f"yt-dlp 退出码 {proc.returncode}: {tail or '(无 stderr)'}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterProcessError(
                f"yt-dlp stdout 无法解析为 JSON: {proc.stdout[:_STDERR_TAIL_CHARS]!r}"
            ) from exc


class GenericYtDlpAdapter:
    """通用 yt-dlp 适配器：一个实现覆盖 YouTube/B 站/抖音等 yt-dlp 支持的平台。"""

    def __init__(
        self,
        *,
        binary: str = "yt-dlp",
        timeout_sec: int = 60,
        download_timeout_sec: int = 600,
        allowlist: tuple[str, ...] = ("youtube.com", "youtu.be", "bilibili.com", "douyin.com"),
        playlist_max_items: int = 50,
    ) -> None:
        self._proc = YtDlpProcess(binary=binary, timeout_sec=timeout_sec)
        self._download_timeout = download_timeout_sec
        self._allowlist = allowlist
        self._playlist_max_items = playlist_max_items

    # --- EPIC-02 实现 ---

    def resolve(self, url: str) -> ResolvedMedia:
        ensure_allowed_url(url, self._allowlist)
        data = self._proc.run_json(
            ["--dump-single-json", "--no-playlist", "--no-warnings", "--skip-download", url]
        )
        return _parse_resolved(data)

    def discover(self, account: AccountRef) -> list[DiscoveredItem]:
        if not account.url:
            raise AdapterError(f"账号 {account.external_id!r} 无 URL，无法 discover")
        ensure_allowed_url(account.url, self._allowlist)
        data = self._proc.run_json(
            [
                "--dump-single-json",
                "--flat-playlist",
                "--no-warnings",
                "--playlist-items",
                f"1:{self._playlist_max_items}",
                account.url,
            ]
        )
        entries = data.get("entries") or []
        # _type='playlist' 是频道页签（Videos/Shorts/Live）等子播放列表，真条目是 'url'
        return [_parse_entry(e) for e in entries if e.get("id") and e.get("_type") != "playlist"]

    # --- EPIC-03：字幕 / 媒体下载 ---

    def fetch_subtitle(
        self, item: ItemRef, language: str | None = None, *, auto: bool = False
    ) -> SubtitleResult | None:
        ensure_allowed_url(item.canonical_url, self._allowlist)
        with tempfile.TemporaryDirectory() as tmp:
            args = [
                "--skip-download",
                "--no-playlist",
                "--no-warnings",
                "--write-auto-subs" if auto else "--write-subs",
                "--sub-langs",
                language or "all",
                "--sub-format",
                "json3/vtt",
                "-o",
                str(Path(tmp) / "%(id)s.%(ext)s"),
                item.canonical_url,
            ]
            self._proc.run_json(args)
            files = sorted(Path(tmp).glob("*.json3")) or sorted(Path(tmp).glob("*.vtt"))
            if not files:
                return None
            f = files[0]
            return SubtitleResult(
                language=language or "",
                content=f.read_bytes(),
                fmt=f.suffix.lstrip("."),
                auto=auto,
            )

    def download_media(self, item: ItemRef, workdir: str | Path) -> DownloadResult:
        # workdir 归编排层所有（TemporaryDirectory 生命周期），adapter 只往里写
        ensure_allowed_url(item.canonical_url, self._allowlist)
        self._proc.run_json(
            [
                "-f",
                "bestaudio/best",
                "--no-playlist",
                "--no-warnings",
                "-o",
                str(Path(workdir) / "%(id)s.%(ext)s"),
                item.canonical_url,
            ],
            timeout_sec=self._download_timeout,
        )
        files = [
            f for f in Path(workdir).iterdir() if f.is_file() and f.suffix != ".part"
        ]  # ENG-3A：跳过 yt-dlp 半成品残留
        if not files:
            raise AdapterProcessError("yt-dlp 未产出下载文件")
        f = files[0]
        return DownloadResult(local_path=str(f), size_bytes=f.stat().st_size)


def _parse_resolved(data: dict) -> ResolvedMedia:
    if data.get("_type") == "playlist" or data.get("entries") is not None:
        raise NotSingleItemError("非单条内容 URL（频道/播放列表），请提供具体视频地址")
    platform = str(data.get("extractor_key", "generic")).lower()
    duration = data.get("duration")
    return ResolvedMedia(
        platform=platform,
        external_item_id=str(data.get("id", "")),
        title=data.get("title"),
        canonical_url=data.get("webpage_url") or "",
        thumbnail_url=data.get("thumbnail"),
        duration_ms=int(duration * 1000) if duration is not None else None,
        item_type="live" if data.get("is_live") else "vod",
        published_at=_parse_published_at(data),
        channel_external_id=data.get("channel_id") or data.get("uploader_id"),
        channel_name=data.get("channel") or data.get("uploader"),
        subtitles=_parse_subtitles(data),
        metadata={k: data[k] for k in _METADATA_KEYS if k in data},
        channel_url=data.get("channel_url"),  # E3
    )


def _parse_published_at(data: dict) -> datetime | None:
    ts = data.get("timestamp") or data.get("release_timestamp")
    if ts:
        return datetime.fromtimestamp(int(ts), tz=UTC)
    raw = data.get("upload_date")  # yt-dlp: "YYYYMMDD"
    if raw and len(str(raw)) == 8 and str(raw).isdigit():
        d = str(raw)
        return datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), tzinfo=UTC)
    return None


def _parse_subtitles(data: dict) -> tuple[SubtitleTrack, ...]:
    tracks: list[SubtitleTrack] = []
    for lang, fmts in (data.get("subtitles") or {}).items():
        if fmts:
            tracks.append(SubtitleTrack(language=lang, is_auto=False))
    for lang, fmts in (data.get("automatic_captions") or {}).items():
        if fmts:
            tracks.append(SubtitleTrack(language=lang, is_auto=True))
    return tuple(tracks)


def _parse_entry(entry: dict) -> DiscoveredItem:
    duration = entry.get("duration")
    return DiscoveredItem(
        external_item_id=str(entry["id"]),
        title=entry.get("title"),
        url=entry.get("url") or entry.get("webpage_url") or "",
        published_at=None,  # flat-playlist 条目无日期；prepare_source_item（EPIC-03）resolve 时回填
        duration_ms=int(duration * 1000) if duration is not None else None,
        metadata={k: entry[k] for k in _ENTRY_METADATA_KEYS if k in entry},
    )


# 注记③：注册时把 YouTube 裸频道地址规范化为可列表的 /videos 页签（ISSUE-003）
_YOUTUBE_CHANNEL_RE = re.compile(
    r"^(https?://[^/]*youtube\.com/(?:channel/UC[\w-]{20,}|@[\w.\-]+|c/[\w.\-]+|user/[\w.\-]+?))/?$"
)
_YOUTUBE_LISTABLE_SUFFIXES = ("/videos", "/streams", "/shorts", "/featured", "/playlists")


def normalize_channel_url(url: str | None, *, platform: str) -> str | None:
    if not url or platform != "youtube":
        return url
    if any(s in url for s in _YOUTUBE_LISTABLE_SUFFIXES) or "/watch" in url or "list=" in url:
        return url
    m = _YOUTUBE_CHANNEL_RE.match(url.strip().rstrip("/"))
    return f"{m.group(1)}/videos" if m else url
