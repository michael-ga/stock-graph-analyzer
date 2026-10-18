from __future__ import annotations
from sqlalchemy import select
from stockanalyzer.db.models import SwingWatchItem, User
from stockanalyzer.db.session import session_scope


def normalize(ticker: str) -> str:
    return ticker.strip().upper()


class SwingWatchRepository:
    def list(self, user_id: str) -> list[str]:
        with session_scope() as s:
            return list(s.scalars(select(SwingWatchItem.ticker).where(SwingWatchItem.user_id == user_id).order_by(SwingWatchItem.created_at, SwingWatchItem.id)))

    def list_assignments(self) -> list[tuple[str, str]]:
        """Return persisted radar owners/tickers for active users only."""
        with session_scope() as s:
            rows = s.execute(
                select(SwingWatchItem.user_id, SwingWatchItem.ticker)
                .join(User, User.id == SwingWatchItem.user_id)
                .where(User.is_active.is_(True))
                .order_by(SwingWatchItem.created_at, SwingWatchItem.id)
            ).all()
            return [(str(user_id), str(ticker)) for user_id, ticker in rows]

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
            item = s.scalar(
                select(SwingWatchItem)
                .where(
                    SwingWatchItem.user_id == user_id,
                    SwingWatchItem.ticker == normalize(ticker),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if item is not None:
                s.delete(item)
        return self.list(user_id)

    def lock_active_assignment(self, session, user_id: str, ticker: str) -> bool:
        """Lock current owner then membership, and revalidate from the database."""
        user = session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or not user.is_active:
            return False
        item = session.scalar(
            select(SwingWatchItem)
            .where(
                SwingWatchItem.user_id == user_id,
                SwingWatchItem.ticker == normalize(ticker),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return item is not None

    def get_notice_level(self, user_id: str, ticker: str) -> int:
        with session_scope() as s:
            item = s.scalar(select(SwingWatchItem).where(SwingWatchItem.user_id == user_id, SwingWatchItem.ticker == normalize(ticker)))
            return item.last_notice_level if item else 0

    def set_notice_level(self, user_id: str, ticker: str, level: int) -> None:
        with session_scope() as s:
            item = s.scalar(select(SwingWatchItem).where(SwingWatchItem.user_id == user_id, SwingWatchItem.ticker == normalize(ticker)))
            if item:
                item.last_notice_level = int(level)

    def claim_notice_level(self, user_id: str, ticker: str, level: int) -> int | None:
        """Atomically swap a radar's notice level and return its previous value."""
        with session_scope() as s:
            item = s.scalar(
                select(SwingWatchItem)
                .where(
                    SwingWatchItem.user_id == user_id,
                    SwingWatchItem.ticker == normalize(ticker),
                )
                .with_for_update()
            )
            if item is None:
                return None
            previous = int(item.last_notice_level)
            if previous != int(level):
                item.last_notice_level = int(level)
            return previous
