from sqlalchemy import select

from app.db.models import Viewpoint
from app.repositories.base import BaseRepository


class ViewpointRepository(BaseRepository[Viewpoint]):
    model = Viewpoint

    def list_by_creator_topic(self, creator_id: int, topic_id: int) -> list[Viewpoint]:
        """人物-主题时间线（D9）：as_of_date DESC NULLS LAST，再按 created_at DESC。"""
        stmt = (
            select(Viewpoint)
            .where(Viewpoint.creator_id == creator_id, Viewpoint.topic_id == topic_id)
            .order_by(Viewpoint.as_of_date.desc().nulls_last(), Viewpoint.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())
