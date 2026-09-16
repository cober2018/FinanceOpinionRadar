from fastapi import APIRouter

from app.api.v1 import source_items

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(source_items.router)
