from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router

app = FastAPI(title="Finance Opinion Radar", version="0.1.0")
app.include_router(api_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# 单入口：web 构建产物（apps/web/dist，make build-web 生成）由 API 直接托管——
# 一个端口同时服务页面与 /api/*；未构建时不挂载（纯 API / 前端 dev server 模式）。
_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if _dist.is_dir() and (_dist / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")
