from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import Base


class BaseRepository[ModelT: Base]:
    """所有仓库返回 ORM 领域类型，禁止裸 dict（执行计划 RAD-012）。"""

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, obj_id: int) -> ModelT | None:
        return self.session.get(self.model, obj_id)

    def list_all(self) -> list[ModelT]:
        return list(self.session.scalars(select(self.model)).all())

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(self.model)) or 0
