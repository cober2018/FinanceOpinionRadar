from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from tests.integration._constants import EXPECTED_TABLES

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "apps" / "api" / "alembic.ini"


def _cfg(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_upgrade_creates_all_prd_tables(migrated_db: str) -> None:
    insp = inspect(create_engine(migrated_db))
    assert EXPECTED_TABLES <= set(insp.get_table_names())


def test_downgrade_base_then_upgrade(migrated_db: str) -> None:
    command.downgrade(_cfg(migrated_db), "base")
    insp = inspect(create_engine(migrated_db))
    assert not (EXPECTED_TABLES & set(insp.get_table_names()))
    command.upgrade(_cfg(migrated_db), "head")
    insp = inspect(create_engine(migrated_db))
    assert EXPECTED_TABLES <= set(insp.get_table_names())
