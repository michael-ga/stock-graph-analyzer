from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import delete, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from stockanalyzer.db.models import OhlcvBar, OhlcvFetch
from stockanalyzer.db.session import session_scope


def _parts(key: str) -> tuple[str, str, str]:
    provider, ticker, timeframe = key.split(":", 2)
    return provider.strip().lower(), ticker.strip().upper(), timeframe


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class OhlcvRepository:
    def store(self, key: str, frame: pd.DataFrame) -> None:
        """Bulk-upsert a complete fetched frame in one transaction."""
        if frame.empty:
            return
        provider, ticker, timeframe = _parts(key)
        fetched = datetime.now(timezone.utc)
        rows = []
        for timestamp, row in frame.iterrows():
            bar_time = pd.Timestamp(timestamp)
            bar_time = (
                bar_time.tz_localize("UTC")
                if bar_time.tzinfo is None
                else bar_time.tz_convert("UTC")
            )
            rows.append(
                {
                    "provider": provider,
                    "ticker": ticker,
                    "timeframe": timeframe,
                    "bar_time": bar_time.to_pydatetime(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0) or 0),
                    "fetched_at": fetched,
                }
            )
        with session_scope() as session:
            table = OhlcvBar.__table__
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                # Serialize writers of the SAME snapshot only; never hold a
                # global/session transaction open between fetches.
                session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                                {"key": f"ohlcv:{provider}:{ticker}:{timeframe}"})
            # Remove only candles outside the latest rolling snapshot. Replacing
            # every row on every poll caused millions of dead tuples.
            session.execute(delete(table).where(
                table.c.provider == provider, table.c.ticker == ticker,
                table.c.timeframe == timeframe,
                table.c.bar_time.not_in([row["bar_time"] for row in rows]),
            ))
            insert = pg_insert(table) if dialect == "postgresql" else sqlite_insert(table)
            excluded = insert.excluded
            statement = insert.values(rows).on_conflict_do_update(
                index_elements=["provider", "ticker", "timeframe", "bar_time"],
                set_={
                    "open": excluded.open,
                    "high": excluded.high,
                    "low": excluded.low,
                    "close": excluded.close,
                    "volume": excluded.volume,
                    "fetched_at": excluded.fetched_at,
                },
                where=or_(*(table.c[name].is_distinct_from(excluded[name])
                            for name in ("open", "high", "low", "close", "volume"))),
            )
            session.execute(statement)
            freshness = OhlcvFetch.__table__
            insert_fetch = pg_insert(freshness) if dialect == "postgresql" else sqlite_insert(freshness)
            session.execute(insert_fetch.values(
                provider=provider, ticker=ticker, timeframe=timeframe, fetched_at=fetched,
            ).on_conflict_do_update(
                index_elements=["provider", "ticker", "timeframe"],
                set_={"fetched_at": fetched},
            ))

    def load_stale(self, key: str) -> pd.DataFrame | None:
        provider, ticker, timeframe = _parts(key)
        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(OhlcvBar)
                    .where(
                        OhlcvBar.provider == provider,
                        OhlcvBar.ticker == ticker,
                        OhlcvBar.timeframe == timeframe,
                    )
                    .order_by(OhlcvBar.bar_time)
                )
            )
            if not rows:
                return None
            index = pd.to_datetime([row.bar_time for row in rows], utc=True)
            return pd.DataFrame(
                {
                    "open": [row.open for row in rows],
                    "high": [row.high for row in rows],
                    "low": [row.low for row in rows],
                    "close": [row.close for row in rows],
                    "volume": [row.volume for row in rows],
                },
                index=index,
            )

    def load(self, key: str, ttl: int) -> pd.DataFrame | None:
        provider, ticker, timeframe = _parts(key)
        with session_scope() as session:
            fetched = session.scalar(
                    select(OhlcvFetch.fetched_at).where(
                        OhlcvFetch.provider == provider,
                        OhlcvFetch.ticker == ticker,
                        OhlcvFetch.timeframe == timeframe,
                )
            )
        if fetched is None or datetime.now(timezone.utc) - _aware(fetched) > timedelta(seconds=ttl):
            return None
        return self.load_stale(key)

    def prune(self, before: datetime, timeframes=("1D", "5D")) -> int:
        with session_scope() as session:
            expired = select(OhlcvFetch.provider, OhlcvFetch.ticker, OhlcvFetch.timeframe).where(
                OhlcvFetch.fetched_at < before,
            )
            result = session.execute(
                delete(OhlcvBar).where(
                    tuple_(OhlcvBar.provider, OhlcvBar.ticker, OhlcvBar.timeframe).in_(expired),
                    OhlcvBar.timeframe.in_(timeframes),
                )
            )
            return result.rowcount or 0
