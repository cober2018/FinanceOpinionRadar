"""本产品媒体引擎进程入口（engines/media）——精简同步引擎（验收 A1 重构）。

只暴露雷达实际使用的两个读取端点（douyin.content_detail / douyin.author_posts），
鉴权沿用 dtk 的 X-API-Key → api_keys 表；解析在请求内同步执行
（registry.resolve → FetchService.fetch），不再经由 dtk 的 Task 表 / Redis 任务
队列 / dtk-worker 进程——业务调度本来就在雷达（Celery），引擎内部的那套任务
体系只是跨进程管线包装（design 3：解除对 dtk 完整应用及其独立业务任务体系的
运行依赖）。

保留的运行依赖（design 3「必要身份/凭据/缓存可以保留」）：
  users/api_keys（鉴权）、identities（加密身份池，DTK_SECRET_KEY 解密）、
  proxies（身份出口）、settings（配置快照）、request_log（fetch 内建可观测行）、
  Redis（响应缓存 + 调度租约/熔断键）。
解除的运行依赖：tasks 表读写与 tasks:queue/signal、dtk-worker、submit_and_wait、
  202/task_id、watchlist、archive/content_snapshots 旁路、console/MCP/TikTok 等
  全部其余表面（vendored 源码保留作库，不再装配）。

对外契约不变（evidence/baseline-2）：X-API-Key 头、
  {"success": bool, "data"|"error", "meta"} 信封、
  GET /api/v1/douyin/video（url|aweme_id + include_raw + wait）、
  GET /api/v1/douyin/user/posts（url|sec_user_id + cursor/count + include_raw + wait）。
有意的行为差异：wait 参数仍按 api.max_wait_seconds 校验，但同步执行总能给出
终态结果，不再出现 202/task_id（radar 客户端的 202→超时分支天然不再触发）。
限流：本引擎只服务雷达（compose 端口绑 127.0.0.1），客户端侧已有 download_gate
并发闸，引擎内不再叠加 dtk 的 Redis 固定窗口限流。

运行：uvicorn engines.media.server:app
环境：DTK_SECRET_KEY / DTK_DATABASE_URL / DTK_REDIS_URL（同 compose 注入）
配置：settings 表快照在启动时加载（load_config）；控制台改配置需重启引擎生效。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from dtk.api import envelope
from dtk.api.deps import Principal, _from_api_key
from dtk.api.routes.content import _author_params, _content_params
from dtk.api.routes.support import language, request_id, resolve_count, resolve_wait
from dtk.core.config import BootstrapSettings, Config
from dtk.core.crypto import Cipher
from dtk.core.db import dispose_engine, init_engine, session_scope
from dtk.core.errors import DtkError, Unauthenticated
from dtk.core.logging import configure, get_logger
from dtk.core.redis import close_redis, init_redis
from dtk.core.types import Platform, Scope
from dtk.identity.pool import IdentityPool
from dtk.platforms.paging import max_page_size
from dtk.services.fetch import FetchContext, FetchService
from dtk.services.settings_store import load_config
from dtk.signing import SignerRegistry, native_signers
from dtk.signing.base import RequestSpec as SigningRequestSpec
from dtk.signing.base import SignedParams, SigningSession, StaticFingerprint
from dtk.transport import TransportIdentity, WreqTransport
from dtk.transport.base import Transport
from dtk.worker import registry
from fastapi import FastAPI, Query, Request

log = get_logger(__name__)

#: 引擎装配的端点（radar 适配器 DouyinApiClient 的两个调用面）
_ENDPOINT_VIDEO = "douyin.content_detail"
_ENDPOINT_AUTHOR_POSTS = "douyin.author_posts"


def _build_fetch(settings: BootstrapSettings, config: Config) -> tuple[FetchService, Transport]:
    """装配同步执行管线：pool + scheduler + native 签名 + transport（无任务队列）。

    与 dtk.worker.runtime.build_runtime 的 fetch 半边同构；砍掉的是任务存储、
    周期循环（pool_filler/prober/maintenance/watchlist）与通知渠道。
    """
    from dtk.scheduler.circuit import circuit_config
    from dtk.scheduler.scheduler import Scheduler, SchedulerConfig
    from dtk.worker.runtime import PoolCandidates, ProxyResolver, _signing_policy

    cipher = Cipher(settings.secret_key)
    pool = IdentityPool(cipher)
    scheduler = Scheduler(
        PoolCandidates(pool),
        SchedulerConfig(
            max_wait_seconds=float(config.get("sched.max_wait_seconds")),
            health_prior=float(config.get("pool.health_prior")),
            circuit=circuit_config(config),
        ),
        # 引擎进程不带通知渠道：alerter=None 是 Scheduler 的明文支持形态
    )
    transport: Transport = WreqTransport()
    signer_registry = SignerRegistry(
        native_signers(), None, policy=_signing_policy(lambda: config), on_alert=None
    )

    async def sign(
        platform: Platform, url: str, params: dict[str, Any], sender: TransportIdentity
    ) -> SignedParams:
        return await signer_registry.sign(
            SigningRequestSpec.get(url, {k: str(v) for k, v in params.items()}),
            StaticFingerprint.of(sender.fingerprint),
            SigningSession(
                cookies=sender.cookies,
                proxy_url=sender.proxy_url,
                identity_id=str(sender.id),
            ),
            platform=platform,
        )

    fetch = FetchService(
        scheduler=scheduler,
        pool=pool,
        transport=transport,
        sign=sign,
        proxy_resolver=ProxyResolver(cipher),
        cooldown_base=int(config.get("sched.cooldown_base_seconds")),
        cooldown_max=int(config.get("sched.cooldown_max_seconds")),
    )
    return fetch, transport


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = BootstrapSettings()
    configure(level=settings.log_level, json_output=settings.log_json)
    init_engine(settings.database_url)
    init_redis(settings.redis_url)
    config = await load_config()
    fetch, transport = _build_fetch(settings, config)
    app.state.settings = settings
    app.state.config = config
    app.state.fetch = fetch
    log.info("engine.media.started", endpoints=[_ENDPOINT_VIDEO, _ENDPOINT_AUTHOR_POSTS])
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await transport.close()
        await close_redis()
        await dispose_engine()
        log.info("engine.media.stopped")


app = FastAPI(
    title="radar-engine-media",
    version="2",
    docs_url="/swagger",  # compose healthcheck 探活端点（原镜像同款路径）
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


async def _principal(request: Request) -> Principal:
    """X-API-Key → api_keys 表解析 + douyin:read 门槛（复用 dtk 鉴权实现）。"""
    raw = request.headers.get("x-api-key")
    if not raw:
        raise Unauthenticated("valid credentials are required")
    async with session_scope() as session:
        principal = await _from_api_key(session, raw)
    if principal is None:
        raise Unauthenticated("valid credentials are required")
    principal.require(Scope.DOUYIN_READ)
    return principal


async def _run(request: Request, *, endpoint: str, params: dict[str, Any]) -> Any:
    """同步执行一个端点调用并按 dtk 信封渲染（成功/失败与原 API 形态一致）。"""
    config: Config = request.app.state.config
    fetch: FetchService = request.app.state.fetch
    call = registry.resolve(endpoint, params, config)
    ctx = FetchContext(include_raw=bool(params.get("include_raw")))
    try:
        async with session_scope() as session:
            try:
                result = await fetch.fetch(
                    session,
                    call.platform,
                    call.endpoint,
                    call.params,
                    parse=call.parse,
                    cache_ttl=call.cache_ttl,
                    ctx=ctx,
                )
            except Exception:
                # 在途 attempt 的 request_log 行先落库（session_scope 异常路径会回滚），
                # 与 worker._execute 的提交纪律一致
                with contextlib.suppress(Exception):
                    await session.commit()
                raise
    except DtkError as exc:
        return envelope.failure(
            exc.code,
            request_id(request),
            language=language(request),
            retry_after=exc.retry_after,
            details=exc.details or None,
        )
    data = result.payload
    extra = {
        "endpoint": call.definition.name,
        "platform": call.platform.value,
        "request_id": str(result.request_id),
    }
    cursor = None
    if isinstance(data, dict) and "items" in data and "has_more" in data:
        cursor = {"next": data.get("cursor"), "has_more": bool(data.get("has_more"))}
    return envelope.success(
        data,
        request_id(request),
        cached=result.cached,
        duration_ms=result.duration_ms,
        cursor=cursor,
        extra=extra,
    )


@app.get("/api/v1/douyin/video", summary="One post, video or image album")
async def video(
    request: Request,
    url: str | None = Query(None),
    aweme_id: str | None = Query(None),
    include_raw: bool = Query(False),
    wait: float | None = Query(None),
) -> Any:
    await _principal(request)
    resolve_wait(request, wait)  # wait 只做上限校验：同步执行总能给出终态结果
    params = _content_params(Platform.DOUYIN, url=url, aweme_id=aweme_id)
    params["include_raw"] = include_raw
    return await _run(request, endpoint=_ENDPOINT_VIDEO, params=params)


@app.get("/api/v1/douyin/user/posts", summary="Author post list")
async def user_posts(
    request: Request,
    url: str | None = Query(None),
    sec_user_id: str | None = Query(None),
    cursor: str | None = Query(None),
    count: int | None = Query(None),
    include_raw: bool = Query(False),
    wait: float | None = Query(None),
) -> Any:
    await _principal(request)
    resolve_wait(request, wait)
    params = _author_params(Platform.DOUYIN, url=url, sec_user_id=sec_user_id)
    params.update(
        {
            "cursor": cursor,
            "count": resolve_count(count, maximum=max_page_size(Platform.DOUYIN)),
            "include_raw": include_raw,
        }
    )
    return await _run(request, endpoint=_ENDPOINT_AUTHOR_POSTS, params=params)


@app.exception_handler(DtkError)
async def _dtk_error_handler(request: Request, exc: DtkError) -> Any:
    """鉴权/参数等未进入 _run 的 DtkError 也按统一信封渲染（与原 API 一致）。"""
    return envelope.failure(
        exc.code,
        request_id(request),
        language=language(request),
        retry_after=exc.retry_after,
        details=exc.details or None,
    )
