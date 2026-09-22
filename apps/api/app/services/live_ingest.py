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
from sqlalchemy.orm.attributes import flag_modified

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
    """扫描 StreamCap 目录树，按 (author, date) 分组、index 升序。

    两种实录布局（Task 1 决策记录 + Task 6-Step3 真栈）：
    `<root>/<platform>/<author>/<date>/*.ts`（folder_name_platform 开，bridge 默认）与
    `<root>/<author>/<date>/*.ts`（关）。author 取主播级目录（平台级不进会话键）。
    """
    grouped: dict[tuple[str, str], list[LiveSegment]] = {}
    for d1 in root.iterdir():
        if not d1.is_dir():
            continue
        for d2 in d1.iterdir():
            if not d2.is_dir():
                continue
            if _DATE_DIR_RE.match(d2.name):
                _collect_day(grouped, d1.name, d2)
                continue
            for d3 in d2.iterdir():  # <platform>/<author>/<date>
                if d3.is_dir() and _DATE_DIR_RE.match(d3.name):
                    _collect_day(grouped, d2.name, d3)
    sessions = [
        LiveSessionDir(author=a, date=d, segments=sorted(segs, key=lambda x: x.index))
        for (a, d), segs in grouped.items()
    ]
    sessions.sort(key=lambda x: (x.author, x.date))
    return sessions


def _split_day_sessions(sd: LiveSessionDir, grace_sec: int) -> list[LiveSessionDir]:
    """同日多次开播切分（2026-09-22 李一恩同日午/晚两场实录 bug）：

    日目录内壁钟空洞 > grace 视为一场新的直播（原 F10 只告警不切分，同日加播被
    并进已收尾会话、整场分片被主循环终态守卫跳过）。返回 ≥1 个子会话，index 升序。
    """
    parts: list[LiveSessionDir] = []
    cur: list[LiveSegment] = []
    for seg in sd.segments:
        if cur and seg.mtime - cur[-1].mtime > grace_sec:
            parts.append(LiveSessionDir(author=sd.author, date=sd.date, segments=cur))
            cur = []
        cur.append(seg)
    if cur:
        parts.append(LiveSessionDir(author=sd.author, date=sd.date, segments=cur))
    return parts


def _collect_day(
    grouped: dict[tuple[str, str], list[LiveSegment]], author: str, day_dir: Path
) -> None:
    for f in day_dir.iterdir():
        index = _parse_segment_index(f.name)
        if f.is_file() and index is not None:
            grouped.setdefault((author, day_dir.name), []).append(
                LiveSegment(index=index, path=f, mtime=f.stat().st_mtime)
            )


# base 带 StreamCap 起录时间戳：<base>_YYYY-MM-DD_HH-MM-SS_NNN.ts
_SEG_TS_RE = re.compile(
    r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_(\d{1,4})\.ts\Z", re.IGNORECASE
)


def _parse_segment_index(name: str) -> int | None:
    """文件名 → 会话内序号。重连后每个 base 重新从 _000 计数（Task 6-Step3 实录），
    故带时间戳的文件用 `base 时间戳数字 * 1e4 + base 内序号` 合成（时区无关、单调、唯一）；
    裸 _NNN.ts 沿用原语义。文件名仅用于解析序号，不作存储 key（F7）。"""
    m = _SEG_TS_RE.search(name)
    if m is None:
        m = _SEGMENT_RE.search(name)
        return int(m.group(1)) if m else None
    stamp = int((m.group(1) + m.group(2)).replace("-", ""))
    return stamp * 10_000 + int(m.group(3))


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
        for day in scan_live_dir(root):
            for sd in _split_day_sessions(day, s.live_close_grace_sec):
                item, session_created = _ensure_session_with_flag(session, sd)
                if item is None:
                    continue
                if session_created:
                    # 开播事件驱动：新直播会话出现 → 立即派弹幕采集，不空等 beat 轮询
                    _dispatch_danmaku_on_live(session, item)
                _warn_gaps(sd, s.live_close_grace_sec)
                if item.status in ("transcribed", "extracting", "reviewing", "ready"):
                    # 已收尾/进入抽取链的会话不再扫描（EPIC-04 状态机扩展后的兼容）
                    continue
                if item.status != "transcribing":
                    _advance_chain(session, item)  # F3：discovered→…→transcribing 连续推进
                counters["sessions_active"] += 1
                # live 注册表以本地 dict 为唯一真源：commit/rollback 后不再回读 ORM 元数据
                # （expire_on_commit=True 的会话里，回读会触发刷新并放大竞态窗口）
                base_meta = dict(item.metadata_json or {})
                live_meta = _normalize_live_meta(base_meta)
                _persist_live_meta(item, base_meta)
                session.commit()  # 会话行先落库：分片级 rollback 不得回滚会话本身
                for seg in sd.segments:
                    outcome = _process_segment(session, item, base_meta, live_meta, seg, s, provider)
                    if outcome == "failed":
                        counters["segments_failed"] += 1
                _persist_live_meta(item, base_meta)
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
            _persist_live_meta(item, base_meta)
            outcome = _process_segment(
                session, item, base_meta, live_meta, seg, s, get_transcription_provider()
            )
            _persist_live_meta(item, base_meta)
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


def _resolve_live_account(session, author: str) -> SourceAccount | None:
    """目录 author → 账号：external_id 直配 → creator 同名。

    不再自动建档（2026-09-18 用户实录 bug）：删除主播后磁盘目录残留，自动建档会把
    已删账号以 sec_uid 名义"复活"（还会重新导入旧分片烧转录）。账号一律由 UI 显式
    添加；目录无对应账号 → 返回 None，调用方跳过该会话并留日志。
    """
    existing = (
        session.query(SourceAccount)
        .filter(SourceAccount.platform == "douyin", SourceAccount.external_id == author)
        .one_or_none()
    )
    if existing is not None:
        return existing
    return (
        session.query(SourceAccount)
        .join(Creator, SourceAccount.creator_id == Creator.id)
        .filter(SourceAccount.platform == "douyin", Creator.display_name == author)
        .order_by(SourceAccount.id)
        .first()
    )


def _ensure_session_with_flag(session, sd: LiveSessionDir) -> tuple[SourceItem | None, bool]:
    """_ensure_session + 是否新建：新建=True 用于开播事件（弹幕即时刻派）。"""
    before_ids = {
        i
        for (i,) in session.query(SourceItem.id).filter(SourceItem.item_type == "live").all()
    }
    item = _ensure_session(session, sd)
    return item, (item is not None and item.id not in before_ids)


def _dispatch_danmaku_on_live(session, item) -> None:
    """开播即采集弹幕：派发失败只记日志，不影响转写主链（弹幕是旁路增强）。"""
    import structlog

    log = structlog.get_logger(__name__)
    try:
        from app.services.danmaku.dispatch import dispatch_danmaku_collectors

        result = dispatch_danmaku_collectors(session)
        log.info("danmaku_dispatched_on_live", session_item=item.id, result=result)
    except Exception as exc:  # noqa: BLE001 旁路失败不阻断
        log.warning("danmaku_dispatch_on_live_failed", session_item=item.id, error=str(exc)[:120])


def _ensure_session(session, sd: LiveSessionDir) -> SourceItem | None:
    """F2：会话键精确查重 → 旧键覆盖归并 → 未收尾会话归并（跨零点）→ 新建；无账号 None。

    会话身份（2026-09-22 同日加播修复）：当天第一场沿用日期键（历史存量兼容）；
    日期键会话已存在但未覆盖本场首分片 = 同日再次开播，以「日期键:首分片序号」
    新建独立会话——同一天午/晚两场不是同一场直播。
    """
    account = _resolve_live_account(session, sd.author)
    if account is None:
        logger.warning("live_dir_no_account_skip", author=sd.author, date=sd.date)
        return None
    legacy_key = _SESSION_KEY_FMT.format(external_id=account.external_id, date=sd.date)
    first_index = sd.segments[0].index if sd.segments else None
    legacy = (
        session.query(SourceItem)
        .filter(
            SourceItem.source_account_id == account.id,
            SourceItem.external_item_id == legacy_key,
        )
        .one_or_none()
    )
    if legacy is None:
        key = legacy_key
    elif _live_covers(legacy, first_index):
        # 未收尾则继续并入；已收尾由主循环终态守卫跳过
        return legacy
    else:
        key = f"{legacy_key}:{first_index}"
        exact = (
            session.query(SourceItem)
            .filter(
                SourceItem.source_account_id == account.id,
                SourceItem.external_item_id == key,
            )
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
    title = f"{sd.author} 直播 {sd.date}"
    hhmm = _first_hhmm(first_index)
    if key != legacy_key and hhmm:
        title = f"{title} {hhmm}"
    item, _created = SourceItemRepository(session).upsert_by_external(
        source_account_id=account.id,
        external_item_id=key,
        title=title,
        item_type="live",
    )
    session.flush()
    return item


def _live_covers(item: SourceItem, first_index: int | None) -> bool:
    """日期键会话是否已覆盖该首分片（升级兼容：判定子会话归属旧会话还是新开播）。"""
    if first_index is None:
        return True
    live = (item.metadata_json or {}).get("live") or {}
    seen: set[str] = set()
    for field in ("processed", "deferred", "skipped_segments", "segment_errors", "segment_paths"):
        seen.update(live.get(field) or {})
    return str(first_index) in seen


def _first_hhmm(index: int) -> str:
    """合成序号（base 时间戳 * 1e4 + 序号）→ "HH:MM"；裸 _NNN 序号无时间语义返回空。"""
    s = str(index)
    if len(s) >= 14 and s.isdigit():
        return f"{s[8:10]}:{s[10:12]}"
    return ""


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
        _persist_live_meta(item, base_meta)
        session.flush()
        session.commit()
        logger.warning("live_segment_failed", item_id=item.id, index=seg.index,
                       attempts=entry["attempts"], error=str(exc)[:200])
        return "failed"


def _persist_live_meta(item, base_meta: dict) -> None:
    """注册表落库：worker 会话工厂 expire_on_commit=False，内层 commit 后重赋同一
    dict 对象不产生变更历史，JSON 列就地变更必须显式 flag_modified（Task 5 缺陷，
    Task 6-Step3 真栈抓到：转写落库而 processed 注册表恒空 → 下轮重复转写）。"""
    item.metadata_json = base_meta
    flag_modified(item, "metadata_json")


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
            _persist_live_meta(item, base_meta)
            closed += 1
            logger.info("live_session_closed", item_id=item.id)
    return closed
