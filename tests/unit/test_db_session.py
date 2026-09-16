from unittest.mock import MagicMock

import app.db.session as session_mod
import pytest
from app.db.session import get_db
from sqlalchemy.exc import OperationalError


def test_get_db_translates_operational_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = MagicMock()
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: (lambda: fake_session))
    gen = get_db()
    next(gen)
    with pytest.raises(RuntimeError, match="数据库连接失败"):
        gen.throw(OperationalError("stmt", {}, Exception("boom")))


def test_get_db_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = MagicMock()
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: (lambda: fake_session))
    gen = get_db()
    next(gen)
    gen.close()
    fake_session.close.assert_called_once()
