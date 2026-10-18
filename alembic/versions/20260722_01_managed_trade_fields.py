"""add managed virtual-trade fields and event log

Revision ID: 20260722_01
Revises: 20260720_01
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_01"
down_revision: Union[str, Sequence[str], None] = "20260720_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("init_stop", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column(
        "mfe_pct", sa.Float(), nullable=False, server_default=sa.text("0")))
    op.add_column("trades", sa.Column(
        "mae_pct", sa.Float(), nullable=False, server_default=sa.text("0")))
    op.add_column("trades", sa.Column(
        "stop_moves", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("trades", sa.Column(
        "managed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("trades", sa.Column("entry_rvol", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column(
        "hold_weekend", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE trades SET init_stop = stop WHERE init_stop IS NULL")
    with op.batch_alter_table("trades") as batch_op:
        batch_op.alter_column(
            "init_stop", existing_type=sa.Float(), nullable=False)

    op.create_table(
        "trade_events",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True, nullable=False),
        sa.Column("trade_id", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("old_stop", sa.Float(), nullable=True),
        sa.Column("new_stop", sa.Float(), nullable=True),
        sa.Column("fraction", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_trade_events_trade_id"), "trade_events", ["trade_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_trade_events_trade_id"), table_name="trade_events")
    op.drop_table("trade_events")
    op.drop_column("trades", "hold_weekend")
    op.drop_column("trades", "entry_rvol")
    op.drop_column("trades", "managed")
    op.drop_column("trades", "stop_moves")
    op.drop_column("trades", "mae_pct")
    op.drop_column("trades", "mfe_pct")
    op.drop_column("trades", "init_stop")
