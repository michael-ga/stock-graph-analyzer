"""add user session controls and admin audit events

Revision ID: 20260722_02
Revises: 20260722_01
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260722_02"
down_revision: Union[str, Sequence[str], None] = "20260722_01"
branch_labels = None
depends_on = None

_PG_FUNCTION = "admin_audit_events_reject_mutation"
_PG_TRIGGER = "trg_admin_audit_events_append_only"
_SQLITE_UPDATE_TRIGGER = "trg_admin_audit_events_no_update"
_SQLITE_DELETE_TRIGGER = "trg_admin_audit_events_no_delete"


def _install_append_only_guards() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"""
            CREATE FUNCTION {_PG_FUNCTION}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Admin audit events are append-only';
            END;
            $$
        """)
        op.execute(f"""
            CREATE TRIGGER {_PG_TRIGGER}
            BEFORE UPDATE OR DELETE ON admin_audit_events
            FOR EACH ROW EXECUTE FUNCTION {_PG_FUNCTION}()
        """)
        return
    if op.get_bind().dialect.name == "sqlite":
        for trigger, operation in (
            (_SQLITE_UPDATE_TRIGGER, "UPDATE"),
            (_SQLITE_DELETE_TRIGGER, "DELETE"),
        ):
            op.execute(f"""
                CREATE TRIGGER {trigger}
                BEFORE {operation} ON admin_audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'Admin audit events are append-only');
                END
            """)


def _remove_append_only_guards() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {_PG_TRIGGER} ON admin_audit_events")
        op.execute(f"DROP FUNCTION IF EXISTS {_PG_FUNCTION}()")
        return
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {_SQLITE_UPDATE_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {_SQLITE_DELETE_TRIGGER}")


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "session_revision", sa.Integer(), nullable=False, server_default=sa.text("0")
    ))
    op.add_column("users", sa.Column(
        "must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("target_user_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("actor_user_id", "target_user_id", "action"):
        op.create_index(op.f(f"ix_admin_audit_events_{column}"),
                        "admin_audit_events", [column], unique=False)
    _install_append_only_guards()


def downgrade() -> None:
    _remove_append_only_guards()
    for column in ("action", "target_user_id", "actor_user_id"):
        op.drop_index(op.f(f"ix_admin_audit_events_{column}"),
                      table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "session_revision")
