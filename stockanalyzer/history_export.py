"""Export durable, user-scoped history without caches, accounts, or credentials."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import inspect, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from stockanalyzer.db.models import (
    AnalysisRun, AnalysisTimeframe, Installation, PaperTrade, Trade, TradeEvent,
    TradeIndicator, TradeSignal, TradeSwingCheck, TradeVerdict, User,
)
from stockanalyzer.db.session import session_scope
from stockanalyzer.history_archive import FORMAT, SCHEMA_VERSION, utc_now
from stockanalyzer.version import app_version


def installation_id() -> str:
    with session_scope() as session:
        table = Installation.__table__
        insert = pg_insert(table) if session.get_bind().dialect.name == "postgresql" else sqlite_insert(table)
        session.execute(insert.values(key="id", value=str(uuid.uuid4())).on_conflict_do_nothing(index_elements=["key"]))
        return session.scalar(select(Installation.value).where(Installation.key == "id"))


def _row(model) -> dict:
    row = {}
    for attribute in inspect(type(model)).column_attrs:
        if attribute.key == "user_id":
            continue
        value = getattr(model, attribute.key)
        if isinstance(value, datetime):
            value = (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()
        row[attribute.key] = value
    if isinstance(model, Trade):
        row["app_version"] = (model.snapshot or {}).get("app_version", "unknown")
    if isinstance(model, PaperTrade):
        row["app_version"] = (model.payload or {}).get("app_version", "unknown")
    return row


def collect_history(user_id: str, *, label: str | None = None) -> dict:
    identity = installation_id()
    with session_scope() as session:
        # All table queries see the same committed trade lifecycle on PostgreSQL.
        if session.get_bind().dialect.name == "postgresql":
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise ValueError("An active user is required to export history")
        tables = {}
        trade_ids = select(Trade.id).where(Trade.user_id == user_id)
        analysis_ids = select(AnalysisRun.id).where(AnalysisRun.user_id == user_id)
        for model in (AnalysisRun, Trade, PaperTrade):
            rows = session.scalars(select(model).where(model.user_id == user_id)).all()
            tables[model.__tablename__] = [_row(row) for row in rows]
        for model in (TradeEvent, TradeSignal, TradeIndicator, TradeVerdict, TradeSwingCheck):
            rows = session.scalars(select(model).where(model.trade_id.in_(trade_ids))).all()
            tables[model.__tablename__] = [_row(row) for row in rows]
        rows = session.scalars(select(AnalysisTimeframe).where(AnalysisTimeframe.analysis_run_id.in_(analysis_ids))).all()
        tables["analysis_timeframes"] = [_row(row) for row in rows]
        return {
            "format": FORMAT, "schema_version": SCHEMA_VERSION, "installation_id": identity,
            "participant": {"id": user.id, "label": (label or user.username).strip()},
            "exported_at": utc_now(), "export_app_version": app_version(),
            "counts": {table: len(rows) for table, rows in tables.items()}, "tables": tables,
        }
