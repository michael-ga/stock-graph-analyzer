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
from stockanalyzer.user_management import UserManagementService

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


def test_postgres_concurrent_admin_disables_cannot_remove_all_admins(postgres_database):
    auth = AuthService()
    first = auth.create_user("first-admin", "correct horse battery staple", is_admin=True)
    second = auth.create_user("second-admin", "another sufficiently long password", is_admin=True)

    def disable(pair):
        actor, target = pair
        try:
            UserManagementService().set_active(actor.id, target.id, False)
            return True
        except (ValueError, PermissionError):
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(disable, ((first, second), (second, first))))

    assert sorted(outcomes) == [False, True]
    assert sum(bool(auth.session_user(user.id)) for user in (first, second)) == 1


def test_postgres_virtual_book_lifecycle_and_analytics(postgres_database):
    from stockanalyzer import papertrade, virtualbook as vb

    owner = AuthService().create_user("book-pg-owner", "correct horse battery staple").id
    snap = {"score": 72, "setup": "Pullback to 20-EMA",
            "timeframes": {"6M": {"signals": [{"name": "MACD cross", "direction": "bear",
                                               "strength": 0.5, "category": "momentum"}],
                                  "indicators": {"rsi": 41.0}, "bias_score": 0.2}},
            "checks": [{"name": "Engine bias agrees", "ok": False, "weight": 2}]}
    for trader in ("bot-GO", "me"):                       # one idea, two bots
        vb.open_position(ticker="NOK", trader=trader, entry=14.0, stop=13.3,
                         target=14.8, snapshot=snap, now=1_000_000.0, user_id=owner)
    changed = vb.mark("NOK", 13.2, now=1_000_100.0, user_id=owner)
    assert {c["close_reason"] for c in changed} == {"stop_hit"}

    managed = vb.open_position(ticker="AAA", trader="bot-gap-mgd", entry=100.0,
                               stop=98.0, target=104.0, managed=True, init_stop=98.0,
                               snapshot={"setup": "gap_and_go_orb"}, now=1_000_000.0,
                               user_id=owner)
    vb.manage("AAA", 102.0, now=1_000_100.0, friday_flat=False, user_id=owner)
    assert {t["id"]: t for t in vb.load(user_id=owner)}[managed["id"]]["stop"] == 100.02

    ideas = vb.algorithm_correctness(user_id=owner)["totals"]
    assert ideas["n_ideas"] == 1 and ideas["n_trades"] == 2
    patterns = vb.losing_trade_patterns(user_id=owner)
    assert patterns["n"] == 2
    assert patterns["failed_checks"] == {"Engine bias agrees": 2}
    assert patterns["common_signals"] == {"MACD cross:bear": 2}
    assert patterns["avg_indicators"]["avg_rsi"] == 41.0
    assert vb.stats(user_id=owner)["traders"]["me"]["losses"] == 1

    rec = {"ts": 1_000_000.0, "ticker": "NOK", "level": 70, "entry": 14.0,
           "stop": 13.3, "target": 14.8}
    assert papertrade.record(rec, user_id=owner) is True
    assert papertrade.record(dict(rec), user_id=owner) is False
