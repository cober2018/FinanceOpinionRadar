from app.db.models import Creator as CreatorModel
from app.db.models import Entity, Topic
from app.db.models import SourceAccount as SourceAccountModel
from app.repositories import CreatorRepository, SourceAccountRepository
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from scripts.seed_dev import run_seed

SEED_TOPICS = 5
SEED_ENTITIES = 10
SEED_CREATORS = 3
SEED_ACCOUNTS = 2


def _counts(session) -> dict[str, int]:
    return {
        "creators": CreatorRepository(session).count(),
        "topics": len(session.scalars(select(Topic)).all()),
        "entities": len(session.scalars(select(Entity)).all()),
        "accounts": SourceAccountRepository(session).count(),
    }


def test_seed_creates_expected_rows(migrated_db: str) -> None:
    factory = sessionmaker(bind=create_engine(migrated_db))
    run_seed(factory)
    session = factory()
    try:
        counts = _counts(session)
        assert counts["creators"] == SEED_CREATORS
        assert counts["topics"] == SEED_TOPICS
        assert counts["entities"] == SEED_ENTITIES
        assert counts["accounts"] == SEED_ACCOUNTS
        assert isinstance(session.scalars(select(CreatorModel)).first(), CreatorModel)
        assert isinstance(
            session.scalars(select(SourceAccountModel)).first(), SourceAccountModel
        )
    finally:
        session.close()


def test_seed_is_idempotent(migrated_db: str) -> None:
    factory = sessionmaker(bind=create_engine(migrated_db))
    run_seed(factory)
    run_seed(factory)
    session = factory()
    try:
        counts = _counts(session)
        assert counts["creators"] == SEED_CREATORS
        assert counts["topics"] == SEED_TOPICS
        assert counts["entities"] == SEED_ENTITIES
        assert counts["accounts"] == SEED_ACCOUNTS
    finally:
        session.close()
