"""yt-dlp CLI 子进程封装与通用适配器（RAD-021，C2/ADR-0007）。"""

import json
import subprocess
from datetime import UTC, datetime

from app.services.media.contracts import (
    AccountRef,
    AdapterError,
    AdapterProcessError,
    AdapterTimeoutError,
    DiscoveredItem,
    ResolvedMedia,
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

    def run_json(self, args: list[str]) -> dict:
        argv = [self._binary, *args]
        try:
            proc = subprocess.run(  # noqa: S603  argv 列表直传，无 shell 拼接
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterTimeoutError(f"yt-dlp 超时（>{self._timeout_sec}s）: {args[0]}") from exc
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
        allowlist: tuple[str, ...] = ("youtube.com", "youtu.be", "bilibili.com", "douyin.com"),
        playlist_max_items: int = 50,
    ) -> None:
        self._proc = YtDlpProcess(binary=binary, timeout_sec=timeout_sec)
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
        return [_parse_entry(e) for e in entries if e.get("id")]

    # --- EPIC-03 接口占位（契约完整性优先，实现随 ASR 落地） ---

    def fetch_subtitle(self, item, language: str | None = None):  # type: ignore[no-untyped-def]
        raise NotImplementedError("EPIC-03 RAD-030")

    def download_media(self, item):  # type: ignore[no-untyped-def]
        raise NotImplementedError("EPIC-03 RAD-031")


def _parse_resolved(data: dict) -> ResolvedMedia:
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
