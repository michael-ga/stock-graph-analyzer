"""Compact dashboard snapshots, cache freshness and export identity.

Revision ID: 20261018_01
Revises: 20260722_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_01"
down_revision = "20260722_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("app_version", sa.String(128), nullable=False,
                                            server_default="unknown"))
    op.create_table(
        "ohlcv_fetches",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("ticker", sa.String(32), primary_key=True),
        sa.Column("timeframe", sa.String(16), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Existing bars stay usable during the first worker cycle after migration.
    op.execute("""
        INSERT INTO ohlcv_fetches (provider, ticker, timeframe, fetched_at)
        SELECT provider, ticker, timeframe, MAX(fetched_at)
        FROM ohlcv_bars GROUP BY provider, ticker, timeframe
    """)
    op.create_table(
        "radar_snapshots",
        sa.Column("ticker", sa.String(32), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "installation",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("value", sa.String(128), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("installation")
    op.drop_table("radar_snapshots")
    op.drop_table("ohlcv_fetches")
    op.drop_column("analysis_runs", "app_version")
