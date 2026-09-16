"""分类法模型。

JSONB 形状（docstring-only，正式 schema 推迟 EPIC-04，见 M4）：
- entity.metadata_json: 实体归一化辅助信息（交易所、币种、板块、消歧规则等）。
"""

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Topic(TimestampMixin, IdMixin, Base):
    __tablename__ = "topic"
    __table_args__ = {"comment": "主题词典：canonical_name 唯一"}

    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    topic_type: Mapped[str] = mapped_column(String(30), nullable=False, default="macro")
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Entity(TimestampMixin, IdMixin, Base):
    __tablename__ = "entity"

    entity_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # stock/index/commodity/...
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    symbol: Mapped[str | None] = mapped_column(String(50))
    market: Mapped[str | None] = mapped_column(String(30))
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
