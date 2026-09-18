"""entity_candidate 表 + viewpoint.merge_reason 列（EPIC-04 RAD-044/045）

Revision ID: f2a9c3d7e1b4
Revises: b7e4d2c9a5f1
Create Date: 2026-09-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a9c3d7e1b4"
down_revision: str | Sequence[str] | None = "b7e4d2c9a5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("viewpoint", sa.Column("merge_reason", sa.String(length=200), nullable=True))
    op.create_table(
        "entity_candidate",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("raw_name", sa.String(length=200), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False, server_default="other"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("first_seen_item_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["first_seen_item_id"], ["source_item.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_name", "entity_type", name="uq_entity_candidate_raw_type"),
    )


def downgrade() -> None:
    op.drop_table("entity_candidate")
    op.drop_column("viewpoint", "merge_reason")
