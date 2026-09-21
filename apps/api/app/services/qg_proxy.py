"""青果长效代理客户端 + 代理池联动（2026-09-21，用户购买的 24h 轮换 IP）。

三个能力对应青果 openapi（域名 longterm.proxy.qg.net）：
- query   /query  查询在用 IP（server 即代理地址，通道持有期间 /get 会报 NO_AVAILABLE_CHANNEL）
- extract /get    提取新 IP（24h 轮换产品到期/释放后才能再提取）
- release /delete 释放 IP（task=* 全部；或指定 ip/task）

约定：qg 出口写入 security.proxy_pool 时形如 `http://{key}:{pwd}@{server}`，
同步逻辑只增删带同款前缀的条目，不碰用户手工配置的其他出口。
"""

import httpx
import structlog

from app.db.models import AppSetting
from app.services.live_status import get_security_settings

logger = structlog.get_logger(__name__)

QG_CONFIG_KEY = "proxy_provider_qg"
_DEFAULT_BASE = "https://longterm.proxy.qg.net"


class QgProxyError(Exception):
    """青果接口返回非 SUCCESS（code/message 原样保留供前端展示）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def get_qg_config(session) -> dict:
    row = session.get(AppSetting, QG_CONFIG_KEY)
    return dict(row.value) if row is not None else {"key": "", "auth_pwd": ""}


def save_qg_config(session, key: str, auth_pwd: str) -> dict:
    row = session.get(AppSetting, QG_CONFIG_KEY)
    value = {"key": key.strip(), "auth_pwd": auth_pwd.strip()}
    if row is None:
        row = AppSetting(key=QG_CONFIG_KEY, value=value)
        session.add(row)
    else:
        row.value = value
    session.commit()
    return {"key": value["key"], "auth_pwd": "***" if value["auth_pwd"] else ""}


def proxy_url(server: str, key: str, auth_pwd: str) -> str:
    return f"http://{key}:{auth_pwd}@{server}"


def _prefix(key: str, auth_pwd: str) -> str:
    return f"http://{key}:{auth_pwd}@"


def sync_pool_into_settings(session, servers: list[str], key: str, auth_pwd: str) -> list[str]:
    """把 qg 在用 server 全量替换进 security.proxy_pool（只动 qg 前缀条目，保留手工出口）。"""
    meta = get_security_settings(session)
    pool = [p for p in (meta.get("proxy_pool") or []) if isinstance(p, str)]
    kept = [p for p in pool if not p.startswith(_prefix(key, auth_pwd))]
    updated = [*kept, *(proxy_url(s, key, auth_pwd) for s in servers)]
    row = session.get(AppSetting, "security")
    if row is not None:
        row.value = {**meta, "proxy_pool": updated}
        session.commit()
    return updated


def remove_servers_from_pool(session, servers: set[str], key: str, auth_pwd: str) -> list[str]:
    meta = get_security_settings(session)
    pool = [p for p in (meta.get("proxy_pool") or []) if isinstance(p, str)]
    dead = {_prefix(key, auth_pwd) + s for s in servers}
    updated = [p for p in pool if p not in dead]
    if updated != pool:
        row = session.get(AppSetting, "security")
        if row is not None:
            row.value = {**meta, "proxy_pool": updated}
            session.commit()
    return updated


class QgLongtermClient:
    """青果长效代理 REST 客户端：query/extract/release 三接口 + 异常映射。"""

    def __init__(self, key: str, auth_pwd: str, base: str = _DEFAULT_BASE, timeout: float = 15.0):
        self._key = key
        self._auth_pwd = auth_pwd
        self._client = httpx.Client(base_url=base, timeout=timeout)

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            resp = self._client.get(path, params={"key": self._key, **(params or {})})
        except httpx.HTTPError as exc:
            raise QgProxyError("NETWORK_ERROR", f"青果接口请求失败: {exc}") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise QgProxyError("BAD_RESPONSE", f"青果接口返回非 JSON: {resp.text[:120]}") from exc
        if body.get("code") != "SUCCESS":
            raise QgProxyError(
                str(body.get("code", "UNKNOWN")), str(body.get("message", "未知错误"))
            )
        return body

    def query(self) -> list[dict]:
        """在用 IP 列表（server=代理地址）。"""
        return self._get("/query").get("data") or []

    def extract(self, num: int = 1) -> list[dict]:
        """提取新 IP（24h 轮换产品：通道需先释放/到期）。"""
        body = self._get("/get", {"num": max(1, min(num, 10))})
        return (body.get("data") or {}).get("ips") or []

    def release(self, ip: str | None = None, all: bool = False) -> dict:
        """释放：all=True 释放全部（task=*），否则按 ip。"""
        params: dict = {"task": "*"} if all else {"ip": ip}
        if not all and not ip:
            raise QgProxyError("INVALID_PARAMETER", "释放需指定 ip 或 all=true")
        return self._get("/delete", params)


def get_qg_client(session) -> QgLongtermClient:
    cfg = get_qg_config(session)
    if not cfg.get("key") or not cfg.get("auth_pwd"):
        raise QgProxyError("NOT_CONFIGURED", "青果长效代理未配置（先保存 AuthKey/AuthPwd）")
    return QgLongtermClient(cfg["key"], cfg["auth_pwd"])
