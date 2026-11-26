"""add worker_status (whether the radar worker is alive, for the app to show)

Revision ID: 20261126_01
Revises: 20261102_01
Create Date: 2026-11-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261126_01"
down_revision: Union[str, Sequence[str], None] = "20261102_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_status",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("release", sa.String(length=32), nullable=False),
        sa.Column("build", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("worker_status")
