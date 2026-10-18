from __future__ import annotations
from sqlalchemy import delete, select
from stockanalyzer.db.models import WatchlistItem
from stockanalyzer.db.session import session_scope


def normalize(ticker: str) -> str:
    return ticker.strip().upper()


class WatchlistRepository:
    def list(self, user_id: str) -> list[str]:
        with session_scope() as s:
            return list(s.scalars(select(WatchlistItem.ticker).where(WatchlistItem.user_id == user_id).order_by(WatchlistItem.created_at, WatchlistItem.id)))

    def add(self, user_id: str, ticker: str) -> list[str]:
        ticker = normalize(ticker)
        if ticker:
            with session_scope() as s:
                exists = s.scalar(select(WatchlistItem.id).where(WatchlistItem.user_id == user_id, WatchlistItem.ticker == ticker))
                if not exists:
                    s.add(WatchlistItem(user_id=user_id, ticker=ticker))
        return self.list(user_id)

    def remove(self, user_id: str, ticker: str) -> list[str]:
        with session_scope() as s:
            s.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.ticker == normalize(ticker)))
        return self.list(user_id)

    def toggle(self, user_id: str, ticker: str) -> list[str]:
        ticker = normalize(ticker)
        return self.remove(user_id, ticker) if ticker in self.list(user_id) else self.add(user_id, ticker)

    def contains(self, user_id: str, ticker: str) -> bool:
        return normalize(ticker) in self.list(user_id)
