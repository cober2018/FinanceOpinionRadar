"""系统侧模型。

JSONB 形状（docstring-only，正式 schema 推迟 EPIC-04，见 M4）：
- prompt_version.schema_json: LLM 抽取输出的 JSON Schema 版本化存档。
- job_run.payload_json: 任务入参快照（source_item id、重试参数等），用于重放。
- audit_log.before_json/after_json: 变更前后行快照（人工修改审计，PRD 13.15）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class PromptVersion(TimestampMixin, IdMixin, Base):
    __tablename__ = "prompt_version"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_prompt_version_name_version"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checksum: Mapped[str | None] = mapped_column(String(64))


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


class AuditLog(IdMixin, Base):
    """所有人工修改必须落此表（PRD 13.15）；write 服务推迟至 EPIC-05（ADR-0006）。"""

    __tablename__ = "audit_log"

    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSONB)
    after_json: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
