"""值守桥（Plan #4 Task 6）：直播账号 → StreamCap recordings.json 同步。

StreamCap v1.0.3 无无头模式且配置不热重载（Task 1 决策记录）：
仅当配置 diff 有变化时原子写（tmp+rename）+ docker restart 触发重载；
无变化则完全不动（防重启风暴）。user_settings.json 只补缺不覆盖（E1 节段隔离）。
"""

import json
import subprocess
import uuid
from pathlib import Path

import structlog

from app.core.settings import get_settings

logger = structlog.get_logger(__name__)

# G2：录制分片时长夹紧区间（秒）
SEGMENT_TIME_MIN = 300
SEGMENT_TIME_MAX = 600
# rec_id 确定性命名空间（同 URL 恒同 id，diff 天然稳定）
_REC_ID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "radar://streamcap-recording")

# radar 依赖的最小 user_settings（仅文件缺失时创建，绝不覆盖用户配置）
_USER_SETTINGS_DEFAULT = {
    "language": "Chinese",
    "live_save_path": "/app/downloads",
    "video_format": "TS",
    "segmented_recording_enabled": True,
    "force_https_recording": True,
    "loop_time_seconds": "60",
    "folder_name_platform": True,
    "folder_name_author": True,
    "folder_name_time": True,
    "login_required": False,
}


def clamp_segment_time(sec: int) -> int:
    """G2：monitor_interval_sec → StreamCap segment_time，夹紧到 [300, 600]。"""
    return max(SEGMENT_TIME_MIN, min(SEGMENT_TIME_MAX, int(sec)))


def resolve_room_url(account) -> str | None:
    """值守房间解析：live_room_url 优先；url 旧形态（房间页）兼容；否则 None（跳过同步）。

    主页 URL 不能当直播间用（StreamCap 房间 API 拿不到数据，报误导性 "VR live"）。
    """
    room = (getattr(account, "live_room_url", None) or "").strip() or None
    if room is None:
        url = account.url or ""
        if "live.douyin.com/" in url:
            room = url
    if room and room.startswith("http://"):  # 抖音 handler 注册正则只认 https
        room = "https://" + room[len("http://"):]
    return room


def build_recording(account) -> dict | None:
    """账号 → StreamCap Recording 字典（schema 见 Task 1 决策记录 to_dict 全字段）。

    无可值守房间（既无 live_room_url，url 也非直播间形态）→ None，调用方跳过。
    """
    url = resolve_room_url(account)
    if url is None:
        return None
    return {
        "rec_id": str(uuid.uuid5(_REC_ID_NS, url)),
        "url": url,
        "streamer_name": account.external_id,  # 仅 UI 展示；目录名取自开播 API 的 anchor_name
        "record_format": "TS",
        "quality": "OD",
        "segment_record": True,
        "segment_time": clamp_segment_time(account.monitor_interval_sec),
        "monitor_status": True,
        "scheduled_recording": False,
        "scheduled_start_time": "",
        "monitor_hours": "",
        "recording_dir": "",  # 空 → StreamCap 默认模板 <platform>/<author>/<date>/
        "enabled_message_push": False,
        "platform": "douyin",
        "platform_key": "douyin",
        "only_notify_no_record": False,
        "flv_use_direct_download": False,
    }


def sync_live_monitors(session, repo=None, *, restart: bool = True) -> dict:
    """beat 入口：期望配置与现文件 diff → 仅变化时原子写 + 重启（E2 精神：幂等、最小扰动）。"""
    s = get_settings()
    if not s.recorder_config_path:
        logger.info("recorder_sync_noop", reason="recorder_config_path 未配置")
        return {"changed": False, "noop": "recorder_config_path 未配置"}
    path = Path(s.recorder_config_path)

    if repo is None:
        from app.repositories.source_accounts import SourceAccountRepository

        repo = SourceAccountRepository(session)
    accounts = repo.list_live_monitored()
    desired = [r for a in accounts if (r := build_recording(a)) is not None]

    existing = _load_json(path) if path.exists() else []
    if _strip_volatile(existing) == _strip_volatile(desired):
        return {"changed": False, "monitors": len(desired)}

    # 保留用户侧手工加的字段值：同 URL 的旧条目 rec_id 沿用
    old_by_url = {e.get("url"): e for e in existing if isinstance(e, dict)}
    for rec in desired:
        if rec["url"] in old_by_url and old_by_url[rec["url"]].get("rec_id"):
            rec["rec_id"] = old_by_url[rec["url"]]["rec_id"]

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, desired)
    _ensure_user_settings(path.parent)
    logger.info("recorder_sync_changed", monitors=len(desired), path=str(path))

    if restart:
        _restart_recorder(s.recorder_container_name)
    return {"changed": True, "monitors": len(desired)}


def _restart_recorder(container_name: str) -> None:
    """配置不热重载 → docker restart；打不开 docker 时告警不阻断（部署差异见 README）。"""
    if not container_name:
        logger.warning("recorder_restart_skipped", reason="recorder_container_name 未配置")
        return
    try:
        subprocess.run(  # noqa: S603  argv 列表直传，无 shell 拼接
            ["docker", "restart", container_name],
            check=True,
            capture_output=True,
            timeout=60,
        )
        logger.info("recorder_restarted", container=container_name)
        # StreamCap 监控循环需一次 UI 会话激活（容器启动后）；无头激活见 README 值守小节
        logger.warning("recorder_activation_required", ui="打开一次 StreamCap Web UI 激活监控循环")
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        logger.warning("recorder_restart_failed", container=container_name, error=str(exc)[:200])


def _strip_volatile(entries: list) -> list:
    """diff 时剔除顺序无关项：按 url 排序后再比，避免条目顺序抖动触发重写。"""
    return sorted(
        (e for e in entries if isinstance(e, dict)),
        key=lambda e: e.get("url", ""),
    )


def _load_json(path: Path) -> list:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("recorder_config_unreadable", path=str(path), error=str(exc)[:200])
        return []
    return data if isinstance(data, list) else []


def _atomic_write(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    tmp.replace(path)  # rename 原子替换


def _ensure_user_settings(config_dir: Path) -> None:
    user_settings = config_dir / "user_settings.json"
    if user_settings.exists():
        return  # E1：不覆盖用户/既有配置
    _atomic_write(user_settings, _USER_SETTINGS_DEFAULT)
