from fastapi import APIRouter

from app.api.v1 import (
    danmaku,
    monitoring,
    proxy_pool,
    retention,
    source_accounts,
    source_items,
    transcripts,
    viewpoints,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(source_items.router)
api_router.include_router(source_accounts.router)
api_router.include_router(monitoring.router)
api_router.include_router(transcripts.router)
api_router.include_router(viewpoints.router)
api_router.include_router(danmaku.router)
api_router.include_router(retention.router)
api_router.include_router(proxy_pool.router)
