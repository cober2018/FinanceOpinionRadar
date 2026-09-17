"""直播分片 ingest 编排（Plan #4 Task 5，RAD-LIVE-03/05）。

目录布局为 StreamCap 实录（Task 1 决策记录）：<root>/<author>/<YYYY-MM-DD>/<base>_NNN.TS。
会话 = source_item(item_type='live')，跨零点拆目录但会话以"未收尾优先"归并（F2）；
偏移 = 前序已处理分片 duration_ms 之和（F5：残片/失败不计入）；
transcript 追加复用 TranscriptRepository.replace_for_item（E3：校验规则不重写）。
单飞闸（E2）：pg advisory lock，beat 不去重/多 worker 下防偏移竞态。
"""

import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.models import Creator, SourceAccount, SourceItem
from app.db.session import get_session_factory
from app.domain.pipeline_states import ensure_transition
from app.repositories.media_assets import MediaAssetRepository
from app.repositories.source_items import SourceItemRepository
from app.repositories.transcripts import TranscriptRepository, TranscriptWrite
from app.services.media.audio import normalize_audio

logger = structlog.get_logger(__name__)

# StreamCap 分片命名：{base}_{NNN}.TS（大小写不敏感）；文件名仅用于解析 index，不作存储 key（F7）
_SEGMENT_RE = re.compile(r"_(\d{1,4})\.ts\Z", re.IGNORECASE)
_DATE_DIR_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_SESSION_KEY_FMT = "live:{external_id}:{date}"
_SINGLETON_KEY = 861205300  # pg advisory lock 键（radar live ingest 专用常量）

_ZERO_COUNTERS = {"sessions_active": 0, "segments_pending": 0, "segments_failed": 0}


class LiveSegment:
    __slots__ = ("index", "path", "mtime")

    def __init__(self, index: int, path: Path, mtime: float) -> None:
        self.index = index
        self.path = path
        self.mtime = mtime


class LiveSessionDir:
    __slots__ = ("author", "date", "segments")

    def __init__(self, author: str, date: str, segments: list[LiveSegment]) -> None:
        self.author = author
        self.date = date
        self.segments = segments


def scan_live_dir(root: Path) -> list[LiveSessionDir]:
    """扫描 <root>/<author>/<date>/*.ts 两级布局，按 (author, date) 分组、index 升序。"""
    grouped: dict[tuple[str, str], list[LiveSegment]] = {}
    for author_dir in root.iterdir():
        if not author_dir.is_dir():
            continue
        for day_dir in author_dir.iterdir():
            if not day_dir.is_dir() or not _DATE_DIR_RE.match(day_dir.name):
                continue
            for f in day_dir.iterdir():
                m = _SEGMENT_RE.search(f.name)
                if f.is_file() and m:
                    grouped.setdefault((author_dir.name, day_dir.name), []).append(
                        LiveSegment(index=int(m.group(1)), path=f, mtime=f.stat().st_mtime)
                    )
    sessions = [
        LiveSessionDir(author=a, date=d, segments=sorted(segs, key=lambda x: x.index))
        for (a, d), segs in grouped.items()
    ]
    sessions.sort(key=lambda x: (x.author, x.date))
    return sessions


def ingest_live_segments(session, provider=None) -> dict:
    """beat 扫描入口：单飞闸 → 扫目录 → 建会话 → 串行处理 → 收尾 → 计数行（F12）。"""
    s = get_settings()
    if not s.live_segments_dir:
        logger.info("live_ingest_noop", reason="live_segments_dir 未配置")
        return {**_ZERO_COUNTERS, "noop": "live_segments_dir 未配置"}
    root = Path(s.live_segments_dir)
    if not root.is_dir():
        logger.warning("live_ingest_dir_missing", path=str(root))
        return dict(_ZERO_COUNTERS)

    if not _acquire_singleton(session):
        # E2：抢不到锁立即退出并留单行日志（G3）
        logger.info("live_ingest_skipped_singleton")
        return {"skipped": "singleton"}

    counters = dict(_ZERO_COUNTERS)
    try:
        # 收尾判定先行：静默关闭的是上一轮遗留会话，不计入本轮 sessions_active
        _close_stale_sessions(session, s.live_close_grace_sec)
        for sd in scan_live_dir(root):
            _warn_gaps(sd, s.live_close_grace_sec)
            item = _ensure_session(session, sd)
            if item.status == "transcribed":  # 已收尾会话不再处理
                continue
            if item.status != "transcribing":
                _advance_chain(session, item)  # F3：discovered→…→transcribing 连续推进
            counters["sessions_active"] += 1
            # live 注册表以本地 dict 为唯一真源：commit/rollback 后不再回读 ORM 元数据
            # （expire_on_commit=True 的会话里，回读会触发刷新并放大竞态窗口）
            base_meta = dict(item.metadata_json or {})
            live_meta = _normalize_live_meta(base_meta)
            item.metadata_json = base_meta
            session.commit()  # 会话行先落库：分片级 rollback 不得回滚会话本身
            for seg in sd.segments:
                outcome = _process_segment(session, item, base_meta, live_meta, seg, s, provider)
                if outcome == "failed":
                    counters["segments_failed"] += 1
            item.metadata_json = base_meta
            session.commit()
            counters["segments_pending"] += _count_pending(live_meta)
        session.commit()
    finally:
        _release_singleton(session)
    logger.info("live_ingest_scan", **counters)
    return counters


def prepare_live_segment(item_id: int, segment_index: int, path: str) -> dict:
    """单分片恢复任务（dispatch_live_prepares 派发）：偏移从 processed 注册表现算。"""
    from app.services.transcription import get_transcription_provider

    session = get_session_factory()()
    s = get_settings()
    try:
        if not _acquire_singleton(session):
            logger.info("live_prepare_skipped_singleton", item_id=item_id)
            return {"skipped": "singleton"}
        try:
            item = session.get(SourceItem, item_id)
            if item is None or item.status != "transcribing":
                return {"skipped": "session_not_active"}
            seg = LiveSegment(
                index=segment_index, path=Path(path), mtime=Path(path).stat().st_mtime
            )
            base_meta = dict(item.metadata_json or {})
            live_meta = _normalize_live_meta(base_meta)
            item.metadata_json = base_meta
            outcome = _process_segment(
                session, item, base_meta, live_meta, seg, s, get_transcription_provider()
            )
            item.metadata_json = base_meta
            session.commit()
            return {"item_id": item_id, "segment": segment_index, "outcome": outcome}
        finally:
            _release_singleton(session)
    finally:
        session.close()


def dispatch_live_prepares() -> int:
    """失败恢复（Task 5 Step 3）：segment_paths 里未处理且未到重试上限的分片重新派发。"""
    from app.worker.celery_app import celery_app

    session = get_session_factory()()
    try:
        items = (
            session.query(SourceItem)
            .filter(SourceItem.item_type == "live", SourceItem.status == "transcribing")
            .all()
        )
        dispatched = 0
        for item in items:
            live_meta = (item.metadata_json or {}).get("live") or {}
            paths: dict = live_meta.get("segment_paths") or {}
            processed: dict = live_meta.get("processed") or {}
            errors: dict = live_meta.get("segment_errors") or {}
            max_attempts = get_settings().live_segment_max_attempts
            for key, path in sorted(paths.items()):
                if key in processed:
                    continue
                if errors.get(key, {}).get("attempts", 0) >= max_attempts:
                    continue
                celery_app.send_task("prepare_live_segment", args=[item.id, int(key), path])
                dispatched += 1
        logger.info("dispatch_live_prepares", dispatched=dispatched)
        return dispatched
    finally:
        session.close()


# --- 内部 ---


def _acquire_singleton(session) -> bool:
    got = session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": _SINGLETON_KEY}
    ).scalar()
    session.commit()
    return bool(got)


def _release_singleton(session) -> None:
    session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SINGLETON_KEY})
    session.commit()


def _warn_gaps(sd: LiveSessionDir, grace_sec: int) -> None:
    """F10：壁钟空洞 > grace 视为录制中断，warning 观测不阻断。"""
    for prev, cur in zip(sd.segments, sd.segments[1:], strict=False):
        if cur.mtime - prev.mtime > grace_sec:
            logger.warning(
                "live_segment_gap", author=sd.author, date=sd.date,
                after_index=prev.index, hole_sec=int(cur.mtime - prev.mtime),
            )


def _resolve_live_account(session, author: str) -> SourceAccount:
    """目录 author → 账号：external_id 直配 → creator 同名 → 自动建档（enabled=False 不入轮询）。"""
    existing = (
        session.query(SourceAccount)
        .filter(SourceAccount.platform == "douyin", SourceAccount.external_id == author)
        .one_or_none()
    )
    if existing is not None:
        return existing
    by_creator = (
        session.query(SourceAccount)
        .join(Creator, SourceAccount.creator_id == Creator.id)
        .filter(SourceAccount.platform == "douyin", Creator.display_name == author)
        .order_by(SourceAccount.id)
        .first()
    )
    if by_creator is not None:
        return by_creator
    creator = Creator(display_name=author, status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=author,  # 主播昵称即身份；真实账号的 external_id 是 sec_uid，无碰撞
        enabled=False,  # 录制账号不参与发现轮询（list_due/list_live_monitored 均滤 enabled）
    )
    session.add(account)
    session.flush()
    return account


def _ensure_session(session, sd: LiveSessionDir) -> SourceItem:
    """F2：精确键查重 → 未收尾会话归并（跨零点延续）→ 新建。"""
    account = _resolve_live_account(session, sd.author)
    key = _SESSION_KEY_FMT.format(external_id=account.external_id, date=sd.date)
    exact = (
        session.query(SourceItem)
        .filter(SourceItem.source_account_id == account.id, SourceItem.external_item_id == key)
        .one_or_none()
    )
    if exact is not None:
        return exact
    open_item = (
        session.query(SourceItem)
        .filter(
            SourceItem.source_account_id == account.id,
            SourceItem.item_type == "live",
            SourceItem.status == "transcribing",
        )
        .order_by(SourceItem.id.desc())
        .first()
    )
    if open_item is not None:  # 跨零点：目录日期只做目录键，会话身份未收尾优先
        return open_item
    item, _created = SourceItemRepository(session).upsert_by_external(
        source_account_id=account.id,
        external_item_id=key,
        title=f"{sd.author} 直播 {sd.date}",
        item_type="live",
    )
    session.flush()
    return item


def _advance_chain(session, item: SourceItem) -> None:
    """F3：无真实 resolve/download 阶段，连续推进到 transcribing。"""
    for step in ("resolved", "media_ready", "transcribing"):
        ensure_transition(item.status, step)
        item.status = step
    session.flush()


def _process_segment(
    session, item: SourceItem, base_meta: dict, live_meta: dict, seg: LiveSegment, s, provider
) -> str:
    """单分片处理：幂等 → 重试上限 → 残片跳过 → ASR+落库。返回 processed/skipped/failed。

    只改 live_meta（本地真源），元数据持久化由调用方对 item.metadata_json 的赋值完成。
    """
    processed: dict = live_meta["processed"]
    errors: dict = live_meta["segment_errors"]
    key = str(seg.index)
    if key in processed:
        return "processed"  # 幂等：重跑跳出
    if errors.get(key, {}).get("attempts", 0) >= s.live_segment_max_attempts:  # F4
        return "skipped"
    if len(processed) >= s.live_max_segments_per_session:
        live_meta["deferred"][key] = "max_segments"
        logger.warning("live_session_max_segments", item_id=item.id)
        return "skipped"
    live_meta["segment_paths"][key] = str(seg.path)  # 供失败恢复重派发

    try:
        with tempfile.TemporaryDirectory(prefix=f"live-{item.id}-") as workdir:
            normalized = normalize_audio(seg.path, Path(workdir))
            if normalized.duration_ms < s.live_min_segment_sec * 1000:  # F5：残片不计偏移（sec→ms）
                live_meta["skipped_segments"][key] = "short"
                session.commit()
                return "skipped"
            result = provider.transcribe(str(normalized.path))

            offset_ms = sum(v["duration_ms"] for v in processed.values())
            MediaAssetRepository(session).record(
                item.id,
                asset_type="audio",
                storage_uri=str(normalized.path),
                mime_type="audio/wav",
                size_bytes=normalized.path.stat().st_size,
                sha256=normalized.output_sha256,
                duration_ms=normalized.duration_ms,
            )
            _append_transcript(session, item, result.segments, offset_ms)
        processed[key] = {
            "duration_ms": normalized.duration_ms,
            "at": datetime.now(UTC).isoformat(),
        }
        live_meta["segment_count"] = len(processed)
        live_meta["last_segment_at"] = datetime.now(UTC).isoformat()
        session.commit()
        return "processed"
    except Exception as exc:  # noqa: BLE001  分片级失败记账不中断整场（F4）
        session.rollback()
        entry = live_meta["segment_errors"].setdefault(key, {"attempts": 0})
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["last_error"] = str(exc)[:300]
        # 回滚会 expire 实例（甚至回滚掉未提交的会话行 INSERT）；本地真源重新赋值
        item.metadata_json = base_meta
        session.flush()
        session.commit()
        logger.warning("live_segment_failed", item_id=item.id, index=seg.index,
                       attempts=entry["attempts"], error=str(exc)[:200])
        return "failed"


def _normalize_live_meta(base_meta: dict) -> dict:
    """取/补 live 子对象（对 base_meta 就地挂载，返回 live 本地真源）。"""
    live = dict(base_meta.get("live") or {})
    live.setdefault("processed", {})
    live.setdefault("segment_errors", {})
    live.setdefault("skipped_segments", {})
    live.setdefault("segment_paths", {})
    live.setdefault("deferred", {})
    live.setdefault("segment_count", 0)
    base_meta["live"] = live
    return live


def _append_transcript(session, item: SourceItem, segments, offset_ms: int) -> None:
    """E3：复用 replace_for_item（含时间校验），既有段 + 新段按偏移换算后整体重写。"""
    repo = TranscriptRepository(session)
    writes = [
        TranscriptWrite(start_ms=sg.start_ms, end_ms=sg.end_ms, text=sg.text,
                        confidence=sg.asr_confidence, language=sg.language,
                        speaker=sg.speaker_label)
        for sg in repo.list_for_item(item.id)
    ]
    for seg in segments:
        writes.append(
            TranscriptWrite(
                start_ms=offset_ms + seg.start_ms,
                end_ms=offset_ms + seg.end_ms,
                text=seg.text,
                confidence=getattr(seg, "confidence", None),
                language=getattr(seg, "language", None),
                speaker=getattr(seg, "speaker", None),
            )
        )
    repo.replace_for_item(item.id, writes)


def _count_pending(live_meta: dict) -> int:
    """F12：已见但未处理、且未永久排除（残片/超限跳过不挂 pending）。"""
    processed = set(live_meta.get("processed") or {})
    skipped = set(live_meta.get("skipped_segments") or {})
    deferred = set(live_meta.get("deferred") or {})
    errors = live_meta.get("segment_errors") or {}
    max_attempts = get_settings().live_segment_max_attempts
    return sum(
        1
        for key in set(live_meta.get("segment_paths") or {})
        if key not in processed
        and key not in skipped
        and key not in deferred
        and errors.get(key, {}).get("attempts", 0) < max_attempts
    )


def _close_stale_sessions(session, grace_sec: int) -> int:
    """G1：静默 > grace 的 transcribing 会话收尾（状态机 transcribing→transcribed 已允许）。"""
    cutoff = datetime.now(UTC) - timedelta(seconds=grace_sec)
    items = (
        session.query(SourceItem)
        .filter(SourceItem.item_type == "live", SourceItem.status == "transcribing")
        .all()
    )
    closed = 0
    for item in items:
        last = (item.metadata_json or {}).get("live", {}).get("last_segment_at")
        if last is None:
            continue
        if datetime.fromisoformat(last) < cutoff:
            ensure_transition(item.status, "transcribed")
            item.status = "transcribed"
            base_meta = dict(item.metadata_json or {})
            live_meta = _normalize_live_meta(base_meta)
            live_meta["closed"] = True
            item.metadata_json = base_meta
            closed += 1
            logger.info("live_session_closed", item_id=item.id)
    return closed
