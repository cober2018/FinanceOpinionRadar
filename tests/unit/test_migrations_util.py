from migrations.util import alembic_escape


def test_percent_in_password_is_doubled() -> None:
    # alembic Config 用 %-插值：原始口令中的 % 必须翻倍才能存活 set/get_main_option
    assert alembic_escape("postgresql://u:p%40ss@h/db") == "postgresql://u:p%%40ss@h/db"


def test_no_percent_unchanged() -> None:
    assert alembic_escape("postgresql://u:pw@h/db") == "postgresql://u:pw@h/db"
