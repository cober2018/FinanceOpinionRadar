from sqlalchemy import select

from app.db.models import Creator
from app.repositories.base import BaseRepository


class CreatorRepository(BaseRepository[Creator]):
    model = Creator

    def create(self, *, display_name: str, bio: str | None = None) -> Creator:
        creator = Creator(display_name=display_name, bio=bio)
        self.session.add(creator)
        self.session.flush()
        return creator

    def get_by_name(self, display_name: str) -> Creator | None:
        return self.session.scalar(select(Creator).where(Creator.display_name == display_name))
