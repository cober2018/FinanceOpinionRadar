"""Alembic 配件的可测小工具。"""


def alembic_escape(url: str) -> str:
    """Config.set_main_option 走 %-插值：口令含 % 时必须翻倍，否则 alembic 抛 ValueError。"""
    return url.replace("%", "%%")
