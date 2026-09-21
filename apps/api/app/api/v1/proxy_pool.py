"""IP 代理池管理（青果长效代理）：配置、查询在用、提取、释放 + 代理池自动同步。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import qg_proxy
from app.services.qg_proxy import QgProxyError

router = APIRouter(prefix="/proxy-pool/qg", tags=["proxy-pool"])

DbDep = Annotated[Session, Depends(get_db)]


def _raise_http(exc: QgProxyError) -> None:
    status = 502 if exc.code in ("NETWORK_ERROR", "BAD_RESPONSE") else 409
    raise HTTPException(status_code=status, detail=f"青果接口错误：{exc.message}（{exc.code}）")


class QgConfigBody(BaseModel):
    key: str
    auth_pwd: str


@router.get("/config")
def get_config(session: DbDep):
    cfg = qg_proxy.get_qg_config(session)
    return {"key": cfg.get("key", ""), "auth_pwd": "***" if cfg.get("auth_pwd") else ""}


@router.put("/config")
def put_config(body: QgConfigBody, session: DbDep):
    if not body.key.strip() or not body.auth_pwd.strip():
        raise HTTPException(status_code=422, detail="AuthKey/AuthPwd 均必填")
    return qg_proxy.save_qg_config(session, body.key, body.auth_pwd)


def _qg_entries_in_pool(pool: list[str], prefix: str) -> set[str]:
    """代理池里属于青果前缀的条目 → 其 server 部分。"""
    return {p.removeprefix(prefix) for p in pool if isinstance(p, str) and p.startswith(prefix)}


@router.get("/ips")
def list_ips(session: DbDep):
    """查询在用 IP 并**对齐代理池**（刷新语义）：在用的补入池，已过期/释放的清出池。

    手工配置的其他出口不受影响。
    """
    try:
        client = qg_proxy.get_qg_client(session)
        ips = client.query()
    except QgProxyError as exc:
        _raise_http(exc)
    cfg = qg_proxy.get_qg_config(session)
    servers = [it["server"] for it in ips if it.get("server")]
    pool = qg_proxy.sync_pool_into_settings(session, servers, cfg["key"], cfg["auth_pwd"])
    prefix = qg_proxy._prefix(cfg["key"], cfg["auth_pwd"])
    in_pool = _qg_entries_in_pool(pool, prefix)
    return {
        "ips": [
            {
                "server": it.get("server"),
                "pool_url": qg_proxy.proxy_url(it["server"], cfg["key"], cfg["auth_pwd"])
                if it.get("server")
                else None,
                "in_pool": bool(it.get("server")) and it["server"] in in_pool,
            }
            for it in ips
        ],
        "pool": pool,
    }


@router.post("/extract")
def extract_ips(session: DbDep, num: int = 1):
    """提取新 IP 并自动同步进代理池（24h 轮换产品：通道占用时需先释放）。"""
    try:
        client = qg_proxy.get_qg_client(session)
        cfg = qg_proxy.get_qg_config(session)
        ips = client.extract(num=num)
        servers = [it["server"] for it in ips if it.get("server")]
        pool = qg_proxy.sync_pool_into_settings(session, servers, cfg["key"], cfg["auth_pwd"])
    except QgProxyError as exc:
        _raise_http(exc)
    return {"extracted": ips, "pool": pool}


@router.post("/release")
def release_ips(session: DbDep, ip: str | None = None, all: bool = False):
    """释放 IP（all=true 全部），释放后同步移出代理池。"""
    try:
        client = qg_proxy.get_qg_client(session)
        cfg = qg_proxy.get_qg_config(session)
        client.release(ip=ip, all=all)
    except QgProxyError as exc:
        _raise_http(exc)
    prefix = qg_proxy._prefix(cfg["key"], cfg["auth_pwd"])
    from app.services.live_status import get_security_settings

    pool_now = get_security_settings(session).get("proxy_pool") or []
    if all:
        # 释放全部后 /query 已为空，无法回查在用集——直接按池内 qg 条目清
        removed = _qg_entries_in_pool(pool_now, prefix)
    else:
        removed = {ip} if ip else set()
    pool = qg_proxy.remove_servers_from_pool(session, removed, cfg["key"], cfg["auth_pwd"])
    return {"released": sorted(removed), "pool": pool}
