"""PostgreSQL paper-trade journal repository."""
from __future__ import annotations

import time
from sqlalchemy import select

from stockanalyzer.db.models import PaperTrade
from stockanalyzer.db.session import session_scope


def _dict(row: PaperTrade) -> dict:
    return {"id": row.id, "source_id": row.source_id, "ts": row.ts,
            "date": row.date, "ticker": row.ticker,
            "level": row.level, **(row.payload or {}), "status": row.status,
            "result_pct": row.result_pct}


class PaperTradeRepository:
    def list(self, user_id: str) -> list[dict]:
        with session_scope() as session:
            rows = session.scalars(select(PaperTrade).where(
                PaperTrade.user_id == user_id).order_by(PaperTrade.ts.desc())).all()
            return [_dict(row) for row in rows]

    def insert(self, user_id: str, record: dict, hours: float = 24.0) -> bool:
        ts = float(record.get("ts", time.time()))
        ticker = str(record.get("ticker", "")).upper(); level = int(record.get("level", 0))
        source_id = record.get("source_id")
        with session_scope() as session:
            if source_id:
                duplicate = session.scalar(select(PaperTrade.id).where(
                    PaperTrade.user_id == user_id,
                    PaperTrade.source_id == str(source_id),
                ).limit(1))
            else:
                duplicate = session.scalar(select(PaperTrade.id).where(
                    PaperTrade.user_id == user_id, PaperTrade.ticker == ticker,
                    PaperTrade.level == level,
                    PaperTrade.ts >= ts - hours * 3600,
                    PaperTrade.ts <= ts + hours * 3600,
                ).limit(1))
            if duplicate is not None:
                return False
            core = {"ts", "date", "ticker", "level", "status", "result_pct", "id", "source_id"}
            session.add(PaperTrade(
                source_id=str(source_id) if source_id else None,
                user_id=user_id, ts=ts, date=record.get("date", ""), ticker=ticker,
                level=level, payload={k: v for k, v in record.items() if k not in core},
                status=record.get("status", "open"), result_pct=record.get("result_pct", 0.0),
            ))
            return True

    def update(self, user_id: str, row_id: int, status: str, result_pct: float) -> None:
        with session_scope() as session:
            row = session.scalar(select(PaperTrade).where(
                PaperTrade.id == row_id, PaperTrade.user_id == user_id).with_for_update())
            if row:
                row.status = status; row.result_pct = result_pct
