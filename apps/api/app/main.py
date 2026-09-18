from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router

app = FastAPI(title="Finance Opinion Radar", version="0.1.0")
app.include_router(api_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# 单入口：一个端口服务全部——
#   /            落地页（apps/web/landing，静态原生 HTML/CSS/JS，Get Started → /console/）
#   /console/*   监控台 SPA（apps/web/dist，make build-web 产物，base=/console/）
#   /api/*       接口
# dist 未构建时只挂落地页与 API（前端 dev server 模式走 make web 的 Vite）。
_web_root = Path(__file__).resolve().parents[2] / "web"
_dist = _web_root / "dist"
if _dist.is_dir() and (_dist / "index.html").exists():
    app.mount("/console", StaticFiles(directory=str(_dist), html=True), name="console")
_landing = _web_root / "landing"
if _landing.is_dir():
    app.mount("/", StaticFiles(directory=str(_landing), html=True), name="landing")
