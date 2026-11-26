"""add etf_links (ETF to underlying linkage)

Revision ID: 20261102_01
Revises: 20260722_02
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261102_01"
down_revision: Union[str, Sequence[str], None] = "20260722_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_links",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("etf", sa.String(length=32), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("leverage", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "etf"),
    )
    op.create_index(op.f("ix_etf_links_user_id"), "etf_links", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_etf_links_user_id"), table_name="etf_links")
    op.drop_table("etf_links")
