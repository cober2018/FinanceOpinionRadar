"""模型定义（统一架构：按数据归属归入拥有者模块）。

原 db/models/*.py 拆分迁入；表名、列、约束完全不变（兼容基线见 evidence/baseline-2）。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.runtime.db.base import Base, IdMixin, TimestampMixin


class JobRun(TimestampMixin, IdMixin, Base):
    __tablename__ = "job_run"

    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    source_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_item.id", ondelete="SET NULL")
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    worker: Mapped[str | None] = mapped_column(String(100))
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSONB)



class AppSetting(TimestampMixin, Base):
    """运行时键值配置（前端"安全设置"等类目的持久层；env 是初始默认，DB 可覆盖）。"""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
