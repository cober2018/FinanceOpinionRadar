import pytest
from app.core.settings import Settings
from pydantic import ValidationError

LOCAL_DEV_DATABASE_URL = "postgresql+psycopg://radar:radar@localhost:5432/radar"


def test_dev_defaults_to_local_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    # _env_file=None：隔离仓库根可能存在的 .env（其 DATABASE_URL 会击穿守卫测试）
    settings = Settings(_env_file=None)
    assert settings.env == "dev"
    assert settings.database_url == LOCAL_DEV_DATABASE_URL


def test_non_dev_without_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(env="prod", _env_file=None)
    assert "DATABASE_URL" in str(exc_info.value)


def test_non_dev_with_explicit_database_url_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prod_url = "postgresql+psycopg://prod:secret@db.internal:5432/radar"
    monkeypatch.setenv("DATABASE_URL", prod_url)
    settings = Settings(env="prod", _env_file=None)
    assert settings.database_url == prod_url


def test_whitespace_only_database_url_is_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.delenv("ENV", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(env="prod", _env_file=None)
    assert "DATABASE_URL" in str(exc_info.value)
