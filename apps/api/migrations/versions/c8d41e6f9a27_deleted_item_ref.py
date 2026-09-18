"""deleted_item_ref 墓碑表：物理删除后防 discover 重导（用户指令 2026-09-18）

Revision ID: c8d41e6f9a27
Revises: f2a9c3d7e1b4
Create Date: 2026-09-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d41e6f9a27"
down_revision: str | Sequence[str] | None = "f2a9c3d7e1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deleted_item_ref",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_account_id", sa.Integer(), nullable=False),
        sa.Column("external_item_id", sa.String(length=200), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_account_id"], ["source_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_account_id", "external_item_id", name="uq_deleted_item_ref"),
    )


def downgrade() -> None:
    op.drop_table("deleted_item_ref")
