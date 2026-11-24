from __future__ import annotations
from sqlalchemy import delete, select
from stockanalyzer.db.models import EtfLink
from stockanalyzer.db.session import session_scope


def normalize(ticker: str) -> str:
    return ticker.strip().upper()


def _row(link: EtfLink) -> dict:
    return {"etf": link.etf, "underlying": link.underlying,
            "leverage": link.leverage, "source": link.source}


class EtfLinkRepository:
    def list(self, user_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.scalars(select(EtfLink).where(EtfLink.user_id == user_id)
                             .order_by(EtfLink.etf))
            return [_row(r) for r in rows]

    def get(self, user_id: str, etf: str) -> dict | None:
        with session_scope() as s:
            row = s.scalar(select(EtfLink).where(EtfLink.user_id == user_id,
                                                 EtfLink.etf == normalize(etf)))
            return _row(row) if row else None

    def upsert(self, user_id: str, etf: str, underlying: str, leverage: float,
               source: str) -> bool:
        """Store a link. An ``auto`` link never replaces a ``user`` one.
        Returns True when the row was written."""
        etf, underlying = normalize(etf), normalize(underlying)
        with session_scope() as s:
            row = s.scalar(select(EtfLink).where(EtfLink.user_id == user_id,
                                                 EtfLink.etf == etf))
            if row is None:
                s.add(EtfLink(user_id=user_id, etf=etf, underlying=underlying,
                              leverage=float(leverage), source=source))
                return True
            if row.source == "user" and source != "user":
                return False
            row.underlying, row.leverage, row.source = underlying, float(leverage), source
            return True

    def remove(self, user_id: str, etf: str) -> None:
        with session_scope() as s:
            s.execute(delete(EtfLink).where(EtfLink.user_id == user_id,
                                            EtfLink.etf == normalize(etf)))
