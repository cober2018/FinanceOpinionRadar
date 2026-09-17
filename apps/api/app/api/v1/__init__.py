from fastapi import APIRouter

from app.api.v1 import monitoring, source_accounts, source_items

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(source_items.router)
api_router.include_router(source_accounts.router)
api_router.include_router(monitoring.router)
