"""DB session 装配。

D28/D34：engine 惰性创建并缓存——模块导入不建连（无 DB 也能 import），
首次真实连接失败时抛出带补救指引的可读错误，而非裸 OperationalError。
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def _redacted_target(url: str) -> str:
    # 只暴露 scheme + host/port/db，不打印口令
    scheme, _, rest = url.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://***@{hostpart}"


def get_db() -> Iterator[Session]:
    """FastAPI dependency；首连失败转译为可读错误。"""
    session = get_session_factory()()
    try:
        yield session
    except OperationalError as exc:
        raise RuntimeError(
            f"数据库连接失败（{_redacted_target(get_settings().database_url)}）："
            "请检查 DATABASE_URL 与 docker compose 依赖是否就绪，参考 .env.example"
        ) from exc
    finally:
        session.close()
