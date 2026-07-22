from __future__ import annotations
from sqlalchemy import delete, select
from stockanalyzer.db.models import SwingWatchItem
from stockanalyzer.db.session import session_scope


def normalize(ticker: str) -> str:
    return ticker.strip().upper()


class SwingWatchRepository:
    def list(self, user_id: str) -> list[str]:
        with session_scope() as s:
            return list(s.scalars(select(SwingWatchItem.ticker).where(SwingWatchItem.user_id == user_id).order_by(SwingWatchItem.created_at, SwingWatchItem.id)))

    def add(self, user_id: str, ticker: str) -> list[str]:
        ticker = normalize(ticker)
        if ticker:
            with session_scope() as s:
                exists = s.scalar(select(SwingWatchItem.id).where(SwingWatchItem.user_id == user_id, SwingWatchItem.ticker == ticker))
                if not exists:
                    s.add(SwingWatchItem(user_id=user_id, ticker=ticker))
        return self.list(user_id)

    def remove(self, user_id: str, ticker: str) -> list[str]:
        with session_scope() as s:
            s.execute(delete(SwingWatchItem).where(SwingWatchItem.user_id == user_id, SwingWatchItem.ticker == normalize(ticker)))
        return self.list(user_id)

    def get_notice_level(self, user_id: str, ticker: str) -> int:
        with session_scope() as s:
            item = s.scalar(select(SwingWatchItem).where(SwingWatchItem.user_id == user_id, SwingWatchItem.ticker == normalize(ticker)))
            return item.last_notice_level if item else 0

    def set_notice_level(self, user_id: str, ticker: str, level: int) -> None:
        with session_scope() as s:
            item = s.scalar(select(SwingWatchItem).where(SwingWatchItem.user_id == user_id, SwingWatchItem.ticker == normalize(ticker)))
            if item:
                item.last_notice_level = int(level)
