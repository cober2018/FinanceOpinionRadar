"""控制台 API 路由装配（bootstrap 职责：只组装，不承载业务）。

装配序保持与原 app/api/v1/__init__.py 一致（兼容基线 evidence/baseline-2 §1）。
"""

from fastapi import APIRouter

from app.modules.collection.danmaku_api import router as danmaku_router
from app.modules.collection.monitoring_api import router as monitoring_router
from app.modules.collection.proxy_pool_api import router as proxy_pool_router
from app.modules.content.api_retention import router as retention_router
from app.modules.content.api_source_items import router as source_items_router
from app.modules.content.api_transcripts import router as transcripts_router
from app.modules.delivery.admin_api import router as open_admin_router
from app.modules.insights.api_taxonomy import router as taxonomy_router
from app.modules.insights.api_timeline import router as timeline_router
from app.modules.insights.api_viewpoints import router as viewpoints_router
from app.modules.sources.api import router as source_accounts_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(source_items_router)
api_router.include_router(source_accounts_router)
api_router.include_router(monitoring_router)
api_router.include_router(transcripts_router)
api_router.include_router(viewpoints_router)
api_router.include_router(taxonomy_router)
api_router.include_router(open_admin_router)
api_router.include_router(danmaku_router)
api_router.include_router(retention_router)
api_router.include_router(proxy_pool_router)
api_router.include_router(timeline_router)
