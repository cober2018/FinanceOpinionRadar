"""UTC 时间戳行为（tests/integration：依赖真实 PG 的 timestamptz）。"""


from app.db.models import Creator
from sqlalchemy import text


def test_timestamps_are_tz_aware(db_session) -> None:
    db_session.add(Creator(display_name="A"))
    db_session.commit()
    raw = db_session.execute(text("SELECT created_at FROM creator")).scalar_one()
    assert raw.tzinfo is not None
    assert raw.tzinfo.utcoffset(raw) is not None
