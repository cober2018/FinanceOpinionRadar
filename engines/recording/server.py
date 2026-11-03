"""本产品录制引擎进程入口（engines/recording，headless）——生命周期版（验收 A3）。

复用 vendored StreamCap v1.0.3 的原版 main 协程与全部录制核心，仅以进程内
虚拟 Flet 页面会话替代"第一个浏览器会话连上才启动探测任务"的上游行为。

本版本补齐 design 2.3 的引擎侧生命周期契约：

1. **配置热重载**（解除 recorder_bridge 的 docker restart 前提）：
   每 30s 轮询 recordings.json（mtime+hash 变化才动）；按 url 对账
   增/删/改监控项——新增直接 start_monitor，删除 stop_monitor+移除，
   参数变化先停后换。桥只需原子写文件，引擎自行生效。
2. **停止收尾**（SIGTERM/SIGINT）：先停新派发（recording_enabled=False），
   对每个在录会话 request_stop（vendor 对 ffmpeg 发 SIGINT 让当前分片收尾）。
   vendor 会在 SIGINT 返回前就从 active_recorders 摘掉录制器，所以排空看的是
   ffmpeg 进程是否退出（≤25s），不是 active_recorders 变空——否则事件循环
   提前结束会杀掉还在刷盘的 ffmpeg，短录分片被截成 0 字节。
   然后落盘状态，写停止报告 `<config_dir>/engine-stop-report.json`：
   哪些会话在信号时在录、哪些未在时限内收尾（=未完成分片，交 radar
   live_ingest 的稳定等待语义核对）。
3. **异常退出可观测**：启动时若存在上一轮停止报告，提示上次退出方式；
   异常退出不产生报告（文件缺席即信号），未完成文件一律留在原处待核对。

运行：python engines/recording/server.py
环境变量：
  STREAMCAP_CONFIG_DIR  产品配置目录（recordings.json / user_settings.json 所在 bind）
"""

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "streamcap"
RELOAD_POLL_SEC = 30
DRAIN_TIMEOUT_SEC = 25
# 对账时忽略的录制器自有字段（与 recorder_bridge._strip_volatile 同口径）：
# rec_id 由引擎按 url 指派、recording_dir 开播后物化——两者变化不构成重载
_VOLATILE_KEYS = frozenset({"rec_id", "recording_dir"})
# 参数变化需要"先停后换"的字段（其余字段仅更新字典即可，实际影响面就是这些）；
# monitor_status 单独按布尔语义比较（StreamCap 落盘会把布尔字符串化）。
# segment_record / flv_use_direct_download 影响录制行为本身，翻转必须换装
_RELOAD_FIELDS = ("segment_time", "quality", "record_format", "segment_record",
                  "flv_use_direct_download", "only_notify_no_record",
                  "monitor_hours", "scheduled_recording")


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


class HeadlessPage(MagicMock):
    """进程内虚拟页面：吸收全部 UI 交互，仅 run_task 真实派发任务。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.web = True
        self.route = "/home"
        self.width = 1280
        self.height = 800
        self.controls = []
        self.overlay = []
        self.window = MagicMock()
        self.data = None  # main() 会把 App 实例挂到这里

    def run_task(self, coro, *args):  # 与 flet Page.run_task 同语义
        # 纯 UI 任务（页面切换/渲染）在无头模式下无意义，吸收掉以免噪声与假失败；
        # 生命周期任务（start_periodic_tasks / check_if_live 等）必须真实派发。
        if getattr(coro, "__name__", "") in {"switch_page"}:
            return None
        return asyncio.get_running_loop().create_task(coro(*args))

    def add(self, *_controls):
        return None

    def update(self, *_a, **_k):
        return None

    async def push_route(self, route):
        self.route = route


def _config_signature(raw: str) -> str:
    """归一化签名：忽略录制器自有字段与格式差异（bool/数字字符串化）。"""
    try:
        entries = json.loads(raw)
        norm = sorted(
            (
                {k: str(v) for k, v in e.items() if k not in _VOLATILE_KEYS}
                for e in entries
                if isinstance(e, dict)
            ),
            key=lambda e: e.get("url", ""),
        )
        return hashlib.sha256(json.dumps(norm, ensure_ascii=False,
                                          sort_keys=True).encode()).hexdigest()
    except (json.JSONDecodeError, TypeError):
        return hashlib.sha256(raw.encode()).hexdigest()


async def _reload_recordings(app, desired_raw: str) -> str:
    """recordings.json 对账重载：新增→start_monitor；删除→stop+移除；参数变化→先停后换。

    返回 "noop" | "changed:<n>"。仅在真正发生变化时 persist（防止重载循环）。
    """
    from app.core.recording.record_manager import GlobalRecordingState
    from app.models.recording.recording_model import Recording

    rm = app.record_manager
    try:
        desired = [e for e in json.loads(desired_raw) if isinstance(e, dict)]
    except json.JSONDecodeError:
        return "noop"  # 桥的写入是原子 rename，读到半文件说明有并发写者，下轮再看

    desired_by_url = {e["url"]: e for e in desired if e.get("url")}
    current_by_url = {r.url: r for r in list(rm.recordings)}
    changes = 0

    for url, entry in desired_by_url.items():
        cur = current_by_url.get(url)
        if cur is None:
            rec = Recording.from_dict(entry)
            with GlobalRecordingState.lock:
                GlobalRecordingState.recordings.append(rec)
            if _as_bool(entry.get("monitor_status")):
                # 必须加闸：vendor 的 start_monitor_recording 会把 monitor_status=False
                # 强制翻开并立即开始监控，与 drift 路径"关monitor只登记"的口径不一致
                await rm.start_monitor_recording(rec, auto_save=False)
            changes += 1
            continue
        drifted = any(
            str(getattr(cur, f, None)) != str(entry.get(f))
            for f in _RELOAD_FIELDS
        ) or _as_bool(entry.get("monitor_status")) != _as_bool(cur.monitor_status)
        if drifted:
            await rm.stop_monitor_recording(cur, auto_save=False)
            with GlobalRecordingState.lock, contextlib.suppress(ValueError):
                GlobalRecordingState.recordings.remove(cur)
            rec = Recording.from_dict(entry)
            with GlobalRecordingState.lock:
                GlobalRecordingState.recordings.append(rec)
            if entry.get("monitor_status"):
                await rm.start_monitor_recording(rec, auto_save=False)
            changes += 1

    for url, cur in current_by_url.items():
        if url not in desired_by_url:
            await rm.stop_monitor_recording(cur, auto_save=False)
            with GlobalRecordingState.lock, contextlib.suppress(ValueError):
                GlobalRecordingState.recordings.remove(cur)
            changes += 1

    if changes:
        rm.initialize_dynamic_state()
        await rm.persist_recordings()
    return "noop" if not changes else f"changed:{changes}"


def _write_stop_report(config_dir: Path, app, unfinished: list[dict],
                       drained_ok: bool, in_flight_ids: set[str]) -> None:
    """停止报告：信号时会话清单 + 未收尾清单（radar 侧核对的引擎反馈面）。

    was_recording 用信号到达时的快照（in_flight_ids）。stop_recording 会在
    ffmpeg 真正退出前就清掉 is_recording / stopping_in_progress，收尾后再读
    标志会把「信号时正在录」一律记成 false。
    """
    rm = app.record_manager
    sessions = [
        {
            "rec_id": r.rec_id,
            "url": r.url,
            "streamer_name": r.streamer_name,
            "recording_dir": r.recording_dir or None,
            "was_recording": r.rec_id in in_flight_ids,
        }
        for r in rm.recordings
    ]
    report = {
        # 本地时区（容器 TZ=Asia/Shanghai，与分片日期键同源）
        "stopped_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "graceful": True,
        "drain_complete": drained_ok,
        "sessions": sessions,
        "unfinished": unfinished,  # 时限内未收尾 = 存在未完成分片，留待 radar 稳定等待语义核对
    }
    path = config_dir / "engine-stop-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1))


def _open_ffmpeg_count(app) -> int:
    """尚未退出的 ffmpeg 子进程数。vendor 在发 SIGINT 之前就清空 active_recorders，
    那个集合变空不代表分片已经收尾。"""
    pm = getattr(app, "process_manager", None)
    procs = list(getattr(pm, "ffmpeg_processes", []) or [])
    return sum(1 for p in procs if getattr(p, "returncode", 0) is None)


async def _graceful_stop(app, config_dir: Path) -> None:
    """停止收尾：停新派发 → 在录会话温和停（分片收尾）→ 等 ffmpeg 退出 → 报告。"""
    from app.utils.logger import logger

    rm = app.record_manager
    app.recording_enabled = False  # 周期循环不再发起新的探测/录制

    # 快照必须先于 stop_recording：后者同步清掉 is_recording（见 _write_stop_report）
    in_flight = [r for r in rm.recordings if r.is_recording or r.stopping_in_progress]
    in_flight_ids = {r.rec_id for r in in_flight}
    for rec in in_flight:
        rm.stop_recording(rec, manually_stopped=False)  # request_stop：ffmpeg 收尾当前分片
    print(f"[radar-recording-engine] stopping {len(in_flight)} active recording(s)",
          flush=True)

    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SEC
    while _open_ffmpeg_count(app) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
    drained_ok = _open_ffmpeg_count(app) == 0

    # 时限内 ffmpeg 还活着才算未完成分片。is_recording 会被 stop_recording 提前清掉，
    # 不能拿它判断收尾是否完成。
    unfinished = []
    if not drained_ok:
        unfinished = [
            {
                "rec_id": r.rec_id,
                "url": r.url,
                "recording_dir": r.recording_dir or None,
            }
            for r in rm.recordings
            if r.rec_id in in_flight_ids
        ]
    with contextlib.suppress(Exception):
        await rm.persist_recordings()
    _write_stop_report(config_dir, app, unfinished, drained_ok, in_flight_ids)
    logger.info(
        f"engine stop report: sessions={len(rm.recordings)} "
        f"in_flight={len(in_flight)} unfinished={len(unfinished)} drained={drained_ok}"
    )
    print(
        f"[radar-recording-engine] stop report: in_flight={len(in_flight)} "
        f"unfinished={len(unfinished)} drained={drained_ok}",
        flush=True,
    )


async def _run() -> None:
    os.chdir(VENDOR_DIR)
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    try:
        from dotenv import load_dotenv

        load_dotenv(VENDOR_DIR / ".env")
    except ImportError:
        pass

    config_dir = Path(os.environ.get("STREAMCAP_CONFIG_DIR") or (VENDOR_DIR / "config"))
    if os.environ.get("STREAMCAP_CONFIG_DIR"):
        link = VENDOR_DIR / "config"
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            # 镜像自带默认 config 目录：产品配置目录优先（shutil 覆盖）
            import shutil

            shutil.rmtree(link)
        link.symlink_to(config_dir.resolve())
    config_dir.mkdir(parents=True, exist_ok=True)
    # 自愈：镜像默认 config 文件缺失会导致会话初始化必崩（language.json →
    # SettingsPage.load_language 空 dict；version.json → KeyError('原画')）。
    # 只补缺不覆盖（与 recorder_bridge._ensure_user_settings 同语义 E1）。
    for default in sorted((Path(__file__).resolve().parent / "defaults").iterdir()):
        target = config_dir / default.name
        if not target.exists():
            target.write_text(default.read_text())

    # 上次退出方式可观测：停止报告缺席 = 上次为异常退出（未收尾分片留待 radar 核对）
    stop_report = config_dir / "engine-stop-report.json"
    if stop_report.exists():
        print("[radar-recording-engine] previous run stopped gracefully (report present)",
              flush=True)
    else:
        print("[radar-recording-engine] no previous stop report (first run or unclean exit)",
              flush=True)

    from main import main as streamcap_main

    page = HeadlessPage()
    await streamcap_main(page)  # 返回后 periodic_check 已在后台任务中运行
    app = page.data

    recordings_json = config_dir / "recordings.json"
    last_sig = _config_signature(
        recordings_json.read_text() if recordings_json.exists() else "[]"
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tick = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        tick += 1
        if tick % 60 == 0:  # 存活心跳：运维确认监控循环未随任何会话死亡
            print("[radar-recording-engine] alive", flush=True)
        if app is not None and tick % RELOAD_POLL_SEC == 0 and recordings_json.exists():
            raw = recordings_json.read_text()
            sig = _config_signature(raw)
            if sig != last_sig:
                last_sig = sig
                result = await _reload_recordings(app, raw)
                if result != "noop":
                    print(f"[radar-recording-engine] config reloaded ({result})", flush=True)

    await _graceful_stop(app, config_dir)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
