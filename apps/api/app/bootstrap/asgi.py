import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap.api import api_router
from app.modules.delivery.open_api import router as open_router
from app.modules.delivery.open_api.deps import audit_middleware
from app.runtime import metrics


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # Plan #7：按 DB 意图对账系统服务开关（launchd agent 被清理后自愈）；失败不阻断启动
    from app.runtime import runtime_control
    from app.runtime.db.session import get_session_factory

    try:
        with get_session_factory()() as session:
            runtime_control.sync_runtime_on_startup(session)
    except Exception:  # noqa: BLE001 - 开发环境可能连不上库（如首次未迁移）
        pass
    yield


app = FastAPI(title="Finance Opinion Radar", version="0.1.0", lifespan=_lifespan)
app.include_router(api_router)
app.include_router(open_router)
# 开放接口调用审计（仅 /open/ 前缀；鉴权失败也记一行，api_key_id 为空）
app.middleware("http")(audit_middleware)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    """HTTP 计数/耗时（route 模板取自路由匹配结果，避免路径参数撑爆标签基数）。"""
    if request.url.path == "/metrics":
        return await call_next(request)
    start = time.monotonic()
    response = await call_next(request)
    route = getattr(request.scope.get("route"), "path", request.url.path)
    metrics.inc_http("radar_http_requests_total", str(response.status_code), route)
    metrics.add_http_latency(route, time.monotonic() - start)
    return response


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def prometheus_metrics() -> str:
    """运行指标（Prometheus 文本格式）。采集失败的部分少报不报错（见 metrics 模块）。"""
    return metrics.render()


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# 单入口：一个端口服务全部——
#   /            落地页（apps/web/landing，静态原生 HTML/CSS/JS，Get Started → /console/）
#   /console/*   监控台 SPA（apps/web/dist，make build-web 产物，base=/console/）
#   /api/*       接口
# dist 未构建时只挂落地页与 API（前端 dev server 模式走 make web 的 Vite）。
# parents[3] = apps/（asgi.py 位于 apps/api/app/bootstrap/；曾写成 parents[2]=apps/api，
# 致 dist 挂载永远 miss、/console 404——2026-11-15 生产切换时发现并热修）
_web_root = Path(__file__).resolve().parents[3] / "web"
_dist = _web_root / "dist"
if _dist.is_dir() and (_dist / "index.html").exists():
    app.mount("/console", StaticFiles(directory=str(_dist), html=True), name="console")
_landing = _web_root / "landing"
if _landing.is_dir():
    app.mount("/", StaticFiles(directory=str(_landing), html=True), name="landing")
