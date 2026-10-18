"""Small dashboard reads; never fetch market data from a browser render."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from stockanalyzer.db.models import RadarSnapshot, SwingWatchItem, Trade, User
from stockanalyzer.db.session import session_scope


class RadarSnapshotRepository:
    def save(self, ticker: str, payload: dict) -> None:
        with session_scope() as session:
            table = RadarSnapshot.__table__
            insert = pg_insert(table) if session.get_bind().dialect.name == "postgresql" else sqlite_insert(table)
            now = datetime.now(timezone.utc)
            session.execute(insert.values(
                ticker=ticker.strip().upper(), updated_at=now, payload=payload,
            ).on_conflict_do_update(
                index_elements=["ticker"], set_={"updated_at": now, "payload": payload},
            ))

    def for_user(self, user_id: str) -> dict[str, dict]:
        """Only return stocks this active user tracks or currently holds."""
        membership = select(SwingWatchItem.ticker).where(SwingWatchItem.user_id == user_id)
        holdings = select(Trade.ticker).where(
            Trade.user_id == user_id, Trade.status.in_(("open", "pending")),
        )
        with session_scope() as session:
            if not session.scalar(select(User.is_active).where(User.id == user_id)):
                return {}
            rows = session.scalars(select(RadarSnapshot).where(or_(
                RadarSnapshot.ticker.in_(membership), RadarSnapshot.ticker.in_(holdings),
            ))).all()
            return {row.ticker: {
                **row.payload, "updated_at": row.updated_at.replace(tzinfo=timezone.utc).isoformat(),
            } for row in rows}

    def ages(self) -> dict[str, datetime]:
        with session_scope() as session:
            return {ticker: stamp.replace(tzinfo=timezone.utc) for ticker, stamp in
                    session.execute(select(RadarSnapshot.ticker, RadarSnapshot.updated_at))}
