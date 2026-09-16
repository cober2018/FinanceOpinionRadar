import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from tests.integration._constants import ALL_TABLES, require_test_db_name

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"


def _test_db_url() -> str:
    """优先级：RADAR_TEST_DATABASE_URL > 由 Settings.database_url 派生（库名换 radar_test）。

    派生而非硬编码 5432：本机/CI 端口由 .env（D33）驱动，测试与开发栈同源。
    """
    explicit = os.environ.get("RADAR_TEST_DATABASE_URL")
    if explicit:
        return explicit
    from app.core.settings import get_settings

    base = get_settings().database_url
    return base.rsplit("/", 1)[0] + "/radar_test"


@pytest.fixture(scope="session")
def database_url() -> str:
    return _test_db_url()


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> Iterator[str]:
    require_test_db_name(database_url)
    admin_url = database_url.rsplit("/", 1)[0] + "/radar"
    admin = create_engine(admin_url)
    try:
        with admin.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text("DROP DATABASE IF EXISTS radar_test"))
            conn.execute(text("CREATE DATABASE radar_test"))
    except OperationalError as exc:
        pytest.fail(
            f"测试库管理连接失败（{admin_url}）：请确认 docker compose 依赖已启动、"
            f"radar 角色具备 CREATEDB 权限（见 README Troubleshooting）: {exc}",
            pytrace=False,
        )
    finally:
        admin.dispose()

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    yield database_url


@pytest.fixture
def db_session(migrated_db: str) -> Iterator[Session]:
    engine = create_engine(migrated_db)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.rollback()
    session.close()
    with engine.connect() as conn:
        conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()
