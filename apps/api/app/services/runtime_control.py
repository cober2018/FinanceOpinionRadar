"""系统服务开关（Plan #7）：开机自启 / 看门狗 的 launchd 生命周期控制。

两个开关各对应一个 LaunchAgent（模板在 infra/launchd/，按仓库路径渲染到
~/Library/LaunchAgents/）：
  autostart  com.financeopinionradar.autostart   登录时跑一次 scripts/dev_up.sh up
  watchdog   com.financeopinionradar.watchdog    常驻 scripts/radar_watchdog.sh，掉线拉起

DB app_setting(key="runtime") 存用户意图；launchd 是实际生效面。PUT 即应用；
API 启动时 sync_runtime_on_startup 按 DB 意图对账（plist 被清理后自愈），
对账失败只告警不阻断启动。
"""

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import AppSetting

logger = logging.getLogger(__name__)

RUNTIME_KEY = "runtime"
_DEFAULTS: dict[str, bool] = {"autostart": False, "watchdog": False}
_LABELS: dict[str, str] = {
    "autostart": "com.financeopinionradar.autostart",
    "watchdog": "com.financeopinionradar.watchdog",
}
_REPO_ROOT = Path(__file__).resolve().parents[4]
_TEMPLATE_DIR = _REPO_ROOT / "infra" / "launchd"
# launchd 默认 PATH 不含 brew，docker/redis-cli 等会找不到（Plan #6.5 实测）
_LAUNCHD_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def get_runtime_settings(session: Session) -> dict:
    stored = _stored(session)
    return {**stored, "installed": _installed_state()}


def put_runtime_settings(session: Session, payload: dict) -> dict:
    stored = _stored(session)
    for flag in _LABELS:
        raw = payload.get(flag)
        if raw is None or raw == stored[flag]:
            continue
        if not isinstance(raw, bool):
            # pydantic 会把 "yes"/1 宽松强转 bool，开关必须显式布尔
            raise ValueError(f"{flag} 需为布尔值")
        _apply_flag(flag, raw)  # 失败抛 ValueError → 路由层 422
        stored[flag] = raw
    _save(session, stored)
    return get_runtime_settings(session)


def sync_runtime_on_startup(session: Session) -> None:
    """按 DB 意图对账 launchd 加载态；任何失败不阻断 API 启动。"""
    stored = _stored(session)
    for flag, enabled in stored.items():
        try:
            _apply_flag(flag, enabled)
        except Exception:  # noqa: BLE001 - 对账是尽力而为
            logger.warning("runtime 对账失败（%s=%s），跳过", flag, enabled, exc_info=True)


def _stored(session: Session) -> dict[str, bool]:
    row = session.get(AppSetting, RUNTIME_KEY)
    value = (row.value if row else None) or {}
    out = dict(_DEFAULTS)
    out.update({k: bool(value[k]) for k in _LABELS if k in value})
    return out


def _save(session: Session, stored: dict[str, bool]) -> None:
    row = session.get(AppSetting, RUNTIME_KEY)
    if row is None:
        session.add(AppSetting(key=RUNTIME_KEY, value=dict(stored)))
    else:
        row.value = dict(stored)
    session.commit()


def _apply_flag(flag: str, enabled: bool) -> None:
    label = _LABELS[flag]
    if enabled:
        if _is_loaded(label):
            return
        plist = _install_plist(flag)
        result = _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist))
        if result.returncode != 0:
            raise ValueError(
                f"launchctl bootstrap 失败（{label}）："
                f"{(result.stderr or result.stdout).strip()}"
            )
    elif _is_loaded(label):
        _launchctl("bootout", f"gui/{os.getuid()}/{label}")


def _install_plist(flag: str) -> Path:
    template = _TEMPLATE_DIR / f"{_LABELS[flag]}.plist"
    target_dir = Path.home() / "Library" / "LaunchAgents"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / template.name
    target.write_text(
        template.read_text(encoding="utf-8").replace("__REPO_DIR__", str(_REPO_ROOT)),
        encoding="utf-8",
    )
    return target


def _installed_state() -> dict[str, bool | None]:
    return {flag: _is_loaded(label) for flag, label in _LABELS.items()}


def _is_loaded(label: str) -> bool | None:
    try:
        return _launchctl("print", f"gui/{os.getuid()}/{label}").returncode == 0
    except FileNotFoundError:
        return None


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, timeout=15, check=False
    )
