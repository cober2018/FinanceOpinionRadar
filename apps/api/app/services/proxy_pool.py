"""下载代理池：设置页「安全 → 代理池」配置的出口 IP 接线到媒体下载。

原则（防风控，2026-09-18 与用户对齐）：
- 身份↔出口**稳定绑定**：按 key（账号 id）哈希固定选池中一个代理，绝不按请求轮换
  ——频繁换 IP 的客户端反而更像机器人
- 故障冷却：代理请求失败（403/超时）→ 内存标记冷却 10 分钟，期间跳过
- 池空/全冷却 → 返回 None 走直连（行为与未配置时完全一致）
"""

import time
from hashlib import sha256

import structlog
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

COOLDOWN_SEC = 600


class ProxyPool:
    """进程内冷却状态；池配置每次从 app_setting 实时读（设置页改了立即生效）。"""

    def __init__(self) -> None:
        self._failed_at: dict[str, float] = {}

    def _proxies(self, session: Session) -> list[str]:
        from app.services.live_status import get_security_settings

        try:
            pool = get_security_settings(session).get("proxy_pool") or []
        except Exception:  # noqa: BLE001 设置读取失败不阻断下载
            pool = []
        return [p for p in pool if isinstance(p, str) and p]

    def pick(self, session: Session, key: str) -> str | None:
        """稳定选择：同 key 恒返同一代理（池不变时）；冷却中的代理跳过。"""
        now = time.time()
        self._failed_at = {p: t for p, t in self._failed_at.items() if now - t < COOLDOWN_SEC}
        proxies = [p for p in self._proxies(session) if p not in self._failed_at]
        if not proxies:
            if self._failed_at:
                logger.warning("proxy_pool_all_cooling_down", cooling=len(self._failed_at))
            return None
        digest = sha256(key.encode()).digest()
        return proxies[digest[0] % len(proxies)]

    def report_failure(self, proxy: str | None, error: str = "") -> None:
        if not proxy:
            return
        self._failed_at[proxy] = time.time()
        logger.warning("proxy_marked_cooldown", proxy=proxy[:40], error=error[:120])

    def report_success(self, proxy: str | None) -> None:
        if proxy and proxy in self._failed_at:
            self._failed_at.pop(proxy, None)


_pool = ProxyPool()


def get_proxy_pool() -> ProxyPool:
    return _pool
