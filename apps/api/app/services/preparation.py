"""prepare_source_item 编排（RAD-031，注记①幂等）：resolve → 字幕优先 → ASR 兜底 → transcript。

幂等模型：FOR UPDATE 行锁 + 状态门槛；commit 释放行锁后每阶段重锁复核（ENG-1A），
中途状态即时落库，崩溃后可从 failed 重跑（failed→resolved 是白名单迁移）。
异常一律不重抛（无 autoretry 风暴），失败写 metadata_json.last_error 供手工重试；
补扫只接管 discovered，failed 不自动重试（防失败风暴）。
"""

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import SourceAccount, SourceItem
from app.domain.pipeline_states import InvalidTransitionError, ensure_transition
from app.repositories.media_assets import MediaAssetRepository
from app.repositories.transcripts import TranscriptRepository, TranscriptWrite
from app.services.media.adapters.yt_dlp import normalize_channel_url
from app.services.media.audio import normalize_audio
from app.services.media.contracts import (
    AdapterError,
    ItemRef,
    MediaSourceAdapter,
    ResolvedMedia,
    SubtitleTrack,
)
from app.services.media.subtitles import is_usable, parse_subtitle
from app.services.storage.base import Storage
from app.services.transcription.contracts import TranscriptionProvider

logger = structlog.get_logger(__name__)

_STAGE_CODES = {
    "resolve": "RESOLVE_FAILED",
    "download": "DOWNLOAD_FAILED",
    "normalize": "MEDIA_FFMPEG_FAILED",
    "asr": "ASR_FAILED",
    "persist": "TRANSCRIPT_FAILED",
}

# 失败进度文案：_record_failure 同步覆写 progress，避免前端残留上一阶段的进行时文案
_STAGE_PROGRESS_CN = {
    "resolve": "解析视频信息",
    "download": "从平台拉取视频文件",
    "normalize": "音频标准化",
    "asr": "语音识别",
    "persist": "转录落库",
}


class MediaTooLongError(Exception):
    """CEO-2C：媒体时长超过 prepare_max_media_duration_sec。"""

    code = "MEDIA_TOO_LONG"


class TranscriptValidationError(Exception):
    """CEO-4A：清洗后段落非法（0 段/时间倒置/超限重叠）。"""

    code = "TRANSCRIPT_FAILED"


@dataclass(frozen=True)
class PreparedSegment:
    """两条来源路（字幕/ASR）统一后的段落形状。"""

    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker: str | None = None


def _set_progress(item: SourceItem, phase: str, detail: str = "") -> None:
    """真实进度：阶段变化即写库（metadata.progress），前端轮询列表即可见。"""
    from sqlalchemy.orm.attributes import flag_modified

    meta = dict(item.metadata_json or {})
    meta["progress"] = {"phase": phase, "detail": detail, "at": datetime.now(UTC).isoformat()}
    item.metadata_json = meta
    flag_modified(item, "metadata_json")


def prepare_source_item(
    item_id: int,
    session: Session,
    adapter: MediaSourceAdapter,
    storage: Storage,
    provider: TranscriptionProvider,
) -> dict:
    s = get_settings()
    item = _lock_item(session, item_id)
    if item is None:
        return {"item_id": item_id, "skipped": "missing"}
    if item.status not in ("discovered", "failed"):
        # 注记①：已推进/处理中 → 幂等跳过
        return {"item_id": item_id, "skipped": item.status}
    stage = {"v": "resolve"}  # 可变盒：子阶段更新当前阶段供失败码映射
    try:
        media = _enrich(session, item, adapter)
        dur = media.duration_ms // 1000 if media.duration_ms else "?"
        _set_progress(item, "resolved", f"时长 {dur}s")
        session.commit()
        with tempfile.TemporaryDirectory() as workdir:
            segs, origin, meta = _subtitle_or_asr(
                session, item, adapter, storage, provider, media, Path(workdir), s, stage
            )
            stage["v"] = "persist"
            _persist_transcript(session, item, storage, segs, origin, meta, Path(workdir), s)
        return {
            "item_id": item_id,
            "status": "transcribed",
            "origin": origin,
            "segments": len(segs),
        }
    except Exception as exc:
        return _record_failure(session, item_id, stage["v"], exc)


def _lock_item(session: Session, item_id: int) -> SourceItem | None:
    return session.execute(
        select(SourceItem).where(SourceItem.id == item_id).with_for_update()
    ).scalar_one_or_none()


def _commit_status(session: Session, item: SourceItem, new_status: str) -> None:
    """ENG-1A 阶段间重锁：commit 已释放行锁，推进前重取 FOR UPDATE + 状态门槛复核。"""
    fresh = _lock_item(session, item.id)
    if fresh is None:
        raise RuntimeError(f"item {item.id} 在推进中消失")
    ensure_transition(fresh.status, new_status)
    fresh.status = new_status
    session.commit()


def _enrich(
    session: Session, item: SourceItem, adapter: MediaSourceAdapter
) -> ResolvedMedia:
    """resolve 补全可空字段（只回填 None，与 upsert 刷新语义一致）→ status=resolved。"""
    media = adapter.resolve(item.canonical_url or "")
    if item.title is None:
        item.title = media.title
    if item.thumbnail_url is None:
        item.thumbnail_url = media.thumbnail_url
    if item.published_at is None:
        item.published_at = media.published_at
    if item.duration_ms is None:
        item.duration_ms = media.duration_ms
    if media.channel_url is not None and item.source_account_id is not None:
        # 注册侧 normalize_channel_url 的兜底路径：账号尚无 url 时补规范化地址
        account = session.get(SourceAccount, item.source_account_id)
        if account is not None and account.url is None:
            account.url = normalize_channel_url(media.channel_url, platform=media.platform)
    item.metadata_json = {
        **item.metadata_json,
        "subtitle_languages": [t.language for t in media.subtitles],
    }
    ensure_transition(item.status, "resolved")
    item.status = "resolved"
    session.commit()
    return media


def _pick_track(
    media: ResolvedMedia, preference: tuple[str, ...]
) -> SubtitleTrack | None:
    """语言前缀匹配，manual 优先于 auto。"""
    for lang in preference:
        for want_auto in (False, True):
            for t in media.subtitles:
                if t.is_auto is want_auto and t.language.startswith(lang):
                    return t
    return None


def _subtitle_or_asr(
    session: Session,
    item: SourceItem,
    adapter: MediaSourceAdapter,
    storage: Storage,
    provider: TranscriptionProvider,
    media: ResolvedMedia,
    workdir: Path,
    s,
    stage: dict,
) -> tuple[list[PreparedSegment], str, dict]:
    """字幕优先（best-effort，CEO-2A 失败降级）；否则 ASR 兜底（CEO-2C 时长闸）。"""
    assets = MediaAssetRepository(session)
    track = _pick_track(media, s.subtitle_lang_preference)
    if track is not None:
        try:
            result = adapter.fetch_subtitle(
                _item_ref(item), language=track.language, auto=track.is_auto
            )
            if result is not None:
                segs = parse_subtitle(result)
                if is_usable(segs, min_chars=s.subtitle_min_chars):
                    key = f"subtitles/{item.id}.{track.language}.{result.fmt}"
                    uri = storage.put_file(
                        key, _tmp_file(workdir, "sub", result.fmt, result.content)
                    )
                    assets.record(
                        item.id,
                        asset_type="subtitle",
                        storage_uri=uri,
                        mime_type="text/vtt" if result.fmt == "vtt" else "application/json",
                        size_bytes=len(result.content),
                    )
                    _commit_status(session, item, "media_ready")
                    _commit_status(session, item, "transcribing")
                    prepared = [
                        PreparedSegment(x.start_ms, x.end_ms, x.text) for x in segs
                    ]
                    meta = {
                        "provider": "subtitle",
                        "model": track.language,
                        "language": track.language,
                        "media_sha256": None,
                    }
                    return prepared, "subtitle", meta
        except AdapterError as exc:
            # CEO-2A：字幕是 best-effort 优化，失败记 warning 降级 ASR，不 failed
            logger.warning(
                "subtitle_fetch_degraded_to_asr", item_id=item.id, error=str(exc)
            )

    # --- ASR 路 ---
    stage["v"] = "download"
    if (
        media.duration_ms is not None
        and media.duration_ms > s.prepare_max_media_duration_sec * 1000
    ):
        raise MediaTooLongError(
            f"媒体时长 {media.duration_ms}ms 超过上限 {s.prepare_max_media_duration_sec}s"
        )
    _set_progress(item, "downloading", "从平台拉取视频文件")
    session.commit()
    downloaded = adapter.download_media(_item_ref(item), workdir)
    _set_progress(item, "downloaded", f"{downloaded.size_bytes // (1024 * 1024) or 1}MB")
    session.commit()
    stage["v"] = "normalize"
    normalized = normalize_audio(
        Path(downloaded.local_path),
        workdir,
        binary=s.ffmpeg_binary,
        timeout_sec=s.ffmpeg_timeout_sec,
    )
    stage["v"] = "asr"
    _set_progress(item, "normalizing", "ffmpeg 音频标准化")
    session.commit()
    uri = storage.put_file(f"audio/{item.id}.wav", normalized.path)
    assets.record(
        item.id,
        asset_type="audio",
        storage_uri=uri,
        mime_type="audio/wav",
        size_bytes=normalized.path.stat().st_size,
        sha256=normalized.output_sha256,
        duration_ms=normalized.duration_ms,
    )
    _commit_status(session, item, "media_ready")
    _commit_status(session, item, "transcribing")
    _set_progress(
        item,
        "asr_running",
        f"mlx 转录中（音频 {normalized.duration_ms // 1000}s，约需 1 分钟）",
    )
    session.commit()
    asr_result = provider.transcribe(str(normalized.path))
    _set_progress(item, "asr_done", f"产出 {len(asr_result.segments)} 段文本")
    session.commit()
    prepared = [
        PreparedSegment(x.start_ms, x.end_ms, x.text, x.confidence, x.speaker)
        for x in asr_result.segments
    ]
    meta = {
        "provider": asr_result.provider,
        "model": asr_result.model,
        "language": asr_result.language,
        "media_sha256": normalized.output_sha256,
    }
    return prepared, "asr", meta


def _persist_transcript(
    session: Session,
    item: SourceItem,
    storage: Storage,
    segs: list[PreparedSegment],
    origin: str,
    meta: dict,
    workdir: Path,
    s,
) -> None:
    cleaned = _clean_segments(segs, s.transcript_overlap_tolerance_ms)
    if not cleaned:
        # CEO-4A：空 transcript 是失败不是成功
        raise TranscriptValidationError("清洗后 0 段，拒绝落空 transcript")
    writes = [
        TranscriptWrite(
            start_ms=x.start_ms,
            end_ms=x.end_ms,
            text=x.text,
            confidence=x.confidence,
            language=meta["language"],
            speaker=x.speaker,
        )
        for x in cleaned
    ]
    TranscriptRepository(session).replace_for_item(item.id, writes)
    raw = json.dumps(
        {
            "origin": origin,
            **meta,
            "segments": [
                {
                    "start_ms": x.start_ms,
                    "end_ms": x.end_ms,
                    "text": x.text,
                    "confidence": x.confidence,
                    "speaker": x.speaker,
                }
                for x in cleaned
            ],
        },
        ensure_ascii=False,
    ).encode()
    key = f"transcripts/{item.id}.{meta['provider']}.{meta['model'] or 'na'}.json"
    uri = storage.put_file(key, _tmp_file(workdir, "raw", "json", raw))
    MediaAssetRepository(session).record(
        item.id,
        asset_type="transcript",
        storage_uri=uri,
        mime_type="application/json",
        size_bytes=len(raw),
    )
    item.metadata_json = {
        **item.metadata_json,
        "transcript": {**meta, "segment_count": len(cleaned)},
    }
    item.metadata_json.pop("last_error", None)  # 成功后清掉历史失败残留，避免误读
    item.metadata_json.pop("progress", None)  # 进度只服务进行中/失败态，成功即清
    if item.language is None and meta["language"]:
        item.language = meta["language"]
    _commit_status(session, item, "transcribed")


def _clean_segments(
    segs: list[PreparedSegment], tolerance_ms: int
) -> list[PreparedSegment]:
    """strip/去空/时间合法性/相邻重叠阈值校验，并按起点排序重编号由仓储承载。"""
    cleaned: list[PreparedSegment] = []
    for seg in sorted(segs, key=lambda x: (x.start_ms, x.end_ms)):
        text = seg.text.strip()
        if not text:
            continue
        if seg.start_ms >= seg.end_ms:
            raise TranscriptValidationError(
                f"段时间非法: [{seg.start_ms},{seg.end_ms}) {text[:20]!r}"
            )
        if cleaned and cleaned[-1].end_ms - seg.start_ms > tolerance_ms:
            raise TranscriptValidationError(
                f"段重叠超阈值: prev_end={cleaned[-1].end_ms} start={seg.start_ms}"
                f" > {tolerance_ms}ms"
            )
        cleaned.append(
            PreparedSegment(
                seg.start_ms, seg.end_ms, text, seg.confidence, seg.speaker
            )
        )
    return cleaned


def record_stage_failure(session: Session, item_id: int, stage: str, exc: Exception) -> dict:
    """任务入口构造期失败（如 douyin 未配置，F8）复用 _record_failure 落 last_error。"""
    return _record_failure(session, item_id, stage, exc)


def _record_failure(
    session: Session, item_id: int, stage: str, exc: Exception
) -> dict:
    session.rollback()
    code = getattr(exc, "code", None) or _STAGE_CODES.get(stage, "RESOLVE_FAILED")
    try:
        fresh = _lock_item(session, item_id)
        if fresh is not None and fresh.status != "ready":
            if fresh.status != "failed":
                try:
                    ensure_transition(fresh.status, "failed")
                except InvalidTransitionError:
                    # 终态半路失败不覆盖，仅记日志
                    logger.warning(
                        "prepare_failure_transition_skipped",
                        item_id=item_id,
                        status=fresh.status,
                    )
                    session.rollback()
                    return {"item_id": item_id, "status": fresh.status, "code": code}
            # 已是 failed（重试再败）允许覆写：last_error/progress 必须反映最新一次失败
            fresh.status = "failed"
            fresh.metadata_json = {
                **fresh.metadata_json,
                "last_error": {
                    "code": code,
                    "stage": stage,
                    "message": str(exc)[:500],
                    "at": datetime.now(UTC).isoformat(),
                },
                # 覆写为失败语义，替换掉失败前残留的 downloading 等进行时进度
                "progress": {
                    "phase": "failed",
                    "detail": (
                        f"{_STAGE_PROGRESS_CN.get(stage, stage)}失败：{str(exc)[:80]}"
                    ),
                    "at": datetime.now(UTC).isoformat(),
                },
            }
            session.commit()
    except Exception:
        logger.exception("prepare_failure_recording_failed", item_id=item_id)
        session.rollback()
    logger.error(
        "prepare_failed", item_id=item_id, stage=stage, code=code, error=str(exc)[:200]
    )
    return {"item_id": item_id, "status": "failed", "code": code, "stage": stage}


def _item_ref(item: SourceItem) -> ItemRef:
    return ItemRef(external_item_id=item.external_item_id, canonical_url=item.canonical_url or "")


def _tmp_file(workdir: Path, stem: str, suffix: str, content: bytes) -> Path:
    p = workdir / f"{stem}.{suffix}"
    p.write_bytes(content)
    return p
