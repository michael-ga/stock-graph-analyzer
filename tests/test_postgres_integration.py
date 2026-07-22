from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import IntegrityError

from stockanalyzer.auth import AuthError, AuthService
from stockanalyzer.db.models import Base
from stockanalyzer.db.repositories.rate_limits import RateLimitRepository
from stockanalyzer.db.repositories.trades import TradeRepository
from stockanalyzer.db.repositories.watchlist import WatchlistRepository
from stockanalyzer.db.session import configure_engine

_POSTGRES_URL = os.getenv("POSTGRES_TEST_URL", "")
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith(("postgresql://", "postgresql+psycopg://")),
    reason="POSTGRES_TEST_URL is not configured",
)


@pytest.fixture()
def postgres_database():
    engine = configure_engine(_POSTGRES_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_postgres_user_isolation(postgres_database):
    auth = AuthService()
    owner = auth.create_user("pg-owner", "correct horse battery staple")
    other = auth.create_user("pg-other", "another sufficiently long password")
    watchlist = WatchlistRepository()
    watchlist.add(owner.id, "msft")
    assert watchlist.list(owner.id) == ["MSFT"]
    assert watchlist.list(other.id) == []


def test_postgres_concurrent_failed_logins_lock_account(postgres_database):
    service = AuthService(max_failures=5, lockout_minutes=15)
    service.create_user("lock-owner", "correct horse battery staple")

    def fail_login(_):
        with pytest.raises(AuthError):
            service.authenticate("lock-owner", "wrong password")

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(fail_login, range(5)))
    with pytest.raises(AuthError):
        service.authenticate("lock-owner", "correct horse battery staple")


def test_postgres_prevents_duplicate_active_positions(postgres_database):
    owner = AuthService().create_user("trade-lock-owner", "correct horse battery staple")
    repository = TradeRepository()

    def insert_trade(index):
        trade = {
            "id": f"active{index:04d}", "ticker": "MSFT", "trader": "bot-GO",
            "status": "open", "kind": "immediate", "opened_ts": float(index + 1),
            "opened": "2026-07-20 12:00", "entry": 100.0, "stop": 95.0,
            "target": 110.0, "stake": 1000.0, "shares": 10.0,
            "horizon_days": 3,
        }
        try:
            repository.insert(owner.id, trade)
            return True
        except IntegrityError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        inserted = list(pool.map(insert_trade, range(2)))
    assert sorted(inserted) == [False, True]
    assert len(repository.list(owner.id)) == 1


def test_postgres_daily_counter_is_atomic(postgres_database):
    repository = RateLimitRepository()
    with ThreadPoolExecutor(max_workers=12) as pool:
        values = list(pool.map(lambda _: repository.increment("finnhub", limit=20), range(60)))
    consumed = sorted(value for value in values if value is not None)
    assert consumed == list(range(1, 21))
    assert repository.remaining("finnhub", limit=20) == 0
