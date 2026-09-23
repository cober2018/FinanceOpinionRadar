"""分类法只读 API：观点编辑时标的联想用（词典规模小，全量返回）。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Entity
from app.db.session import get_db

router = APIRouter(prefix="/entities", tags=["taxonomy"])

DbDep = Annotated[Session, Depends(get_db)]


class EntityRow(BaseModel):
    id: int
    entity_type: str
    canonical_name: str
    symbol: str | None = None


@router.get("")
def list_entities(session: DbDep) -> dict:
    rows = session.execute(
        select(Entity).order_by(Entity.entity_type, Entity.canonical_name)
    ).scalars().all()
    return {
        "items": [
            EntityRow(
                id=e.id,
                entity_type=e.entity_type,
                canonical_name=e.canonical_name,
                symbol=e.symbol,
            )
            for e in rows
        ]
    }
