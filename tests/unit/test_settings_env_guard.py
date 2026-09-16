import pytest
from app.core.settings import Settings
from pydantic import ValidationError

LOCAL_DEV_DATABASE_URL = "postgresql+psycopg://radar:radar@localhost:5432/radar"


def test_dev_defaults_to_local_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings()
    assert settings.env == "dev"
    assert settings.database_url == LOCAL_DEV_DATABASE_URL


def test_non_dev_without_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(env="prod")
    assert "DATABASE_URL" in str(exc_info.value)


def test_non_dev_with_explicit_database_url_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prod_url = "postgresql+psycopg://prod:secret@db.internal:5432/radar"
    monkeypatch.setenv("DATABASE_URL", prod_url)
    settings = Settings(env="prod")
    assert settings.database_url == prod_url
