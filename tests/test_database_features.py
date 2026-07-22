from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import pytest

from stockanalyzer.auth import AuthError, AuthService
from stockanalyzer.db.models import Base, TradeEvent
from stockanalyzer.db.repositories.analysis_history import AnalysisHistoryRepository
from stockanalyzer.db.repositories.api_cache import ApiCacheRepository
from stockanalyzer.db.repositories.ohlcv import OhlcvRepository
from stockanalyzer.db.repositories.rate_limits import RateLimitRepository
from stockanalyzer.db.repositories.swingwatch import SwingWatchRepository
from stockanalyzer.db.repositories.trades import TradeRepository
from stockanalyzer.db.repositories.watchlist import WatchlistRepository
from stockanalyzer.db.session import configure_engine, session_scope


@pytest.fixture()
def db(tmp_path):
    engine = configure_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_auth_hashes_password_and_locks_repeated_failures(db):
    auth = AuthService(max_failures=2, lockout_minutes=10)
    user = auth.create_user("owner", "correct horse battery staple", is_admin=True)
    assert user.password_hash != "correct horse battery staple"
    with pytest.raises(AuthError, match="Invalid username or password"):
        auth.authenticate("owner", "wrong")
    with pytest.raises(AuthError, match="Invalid username or password"):
        auth.authenticate("owner", "wrong")
    with pytest.raises(AuthError, match="Invalid username or password"):
        auth.authenticate("owner", "correct horse battery staple")


def test_watchlist_and_swingwatch_are_unique_and_user_isolated(db):
    auth = AuthService()
    first = auth.create_user("first", "password long enough")
    second = auth.create_user("second", "another long password")
    watch = WatchlistRepository()
    assert watch.add(first.id, " msft ") == ["MSFT"]
    assert watch.add(first.id, "MSFT") == ["MSFT"]
    assert watch.list(second.id) == []
    swing = SwingWatchRepository()
    swing.add(first.id, "aapl")
    swing.set_notice_level(first.id, "AAPL", 70)
    assert swing.list(first.id) == ["AAPL"]
    assert swing.get_notice_level(first.id, "AAPL") == 70


def test_rate_limit_increment_is_bounded(db):
    repo = RateLimitRepository()
    assert repo.increment("twelvedata", limit=2) == 1
    assert repo.increment("twelvedata", limit=2) == 2
    assert repo.increment("twelvedata", limit=2) is None
    assert repo.remaining("twelvedata", limit=2) == 0


def test_rate_limit_increment_is_atomic_across_workers(db):
    repo = RateLimitRepository()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: repo.increment("finnhub", limit=10), range(24)))
    consumed = sorted(value for value in results if value is not None)
    assert consumed == list(range(1, 11))
    assert repo.remaining("finnhub", limit=10) == 0


def test_ohlcv_cache_roundtrip_and_freshness(db):
    repo = OhlcvRepository()
    idx = pd.to_datetime(["2026-07-20T10:00:00Z", "2026-07-20T10:01:00Z"])
    frame = pd.DataFrame(
        {"open": [1.0, 2.0], "high": [2.0, 3.0], "low": [0.5, 1.5],
         "close": [1.5, 2.5], "volume": [10.0, 11.0]}, index=idx
    )
    repo.store("yfinance:MSFT:1D", frame)
    loaded = repo.load_stale("yfinance:MSFT:1D")
    assert loaded is not None
    assert loaded["close"].tolist() == [1.5, 2.5]
    assert repo.load("yfinance:MSFT:1D", ttl=60) is not None


def test_api_cache_rejects_expired_payload(db):
    repo = ApiCacheRepository()
    repo.put("profile:MSFT", "finnhub", {"name": "Microsoft"}, ttl_seconds=60)
    assert repo.get("profile:MSFT") == {"name": "Microsoft"}


def test_analysis_history_filters_by_owner_and_exports_safe_csv(db):
    auth = AuthService()
    owner = auth.create_user("history", "history password")
    other = auth.create_user("other", "other password")
    repo = AnalysisHistoryRepository()
    now = datetime.now(timezone.utc)
    repo.save(owner.id, ticker="msft", provider="yfinance", verdict={"label": "BUY"},
              timeframes=[{"timeframe": "1D", "bias_score": 0.7,
                           "trend_direction": "bullish", "bar_count": 10}],
              started_at=now, completed_at=now, idempotency_key="one")
    repo.save(other.id, ticker="AAPL", provider="yfinance", verdict={"label": "SELL"},
              timeframes=[{"timeframe": "1D", "bias_score": -0.4,
                           "trend_direction": "bearish", "bar_count": 10}],
              started_at=now, completed_at=now, idempotency_key="two")
    rows = repo.query(owner.id, ticker="MSFT", limit=25)
    assert len(rows) == 1 and rows[0]["ticker"] == "MSFT"
    detail = repo.get_detail(owner.id, rows[0]["id"])
    assert detail is not None and detail["timeframes"][0]["timeframe"] == "1D"
    assert repo.get_detail(other.id, rows[0]["id"]) is None
    exported = repo.to_csv(rows)
    assert "password" not in exported.lower()
    assert "MSFT" in exported and "AAPL" not in exported


def _managed_trade(trade_id: str, ticker: str = "AAA", trader: str = "bot-gap-mgd",
                   managed: bool = True) -> dict:
    return {
        "id": trade_id, "ticker": ticker, "trader": trader,
        "status": "open", "kind": "immediate", "opened_ts": 1_000_000.0,
        "opened": "1970-01-12 13:46", "activated_ts": 1_000_000.0,
        "entry": 100.0, "stop": 98.0, "target": 104.0, "trigger": None,
        "stake": 1000.0, "shares": 10.0, "horizon_days": 1,
        "managed": managed, "init_stop": 98.0, "entry_rvol": 2.0,
        "hold_weekend": False, "snapshot": {"setup": "gap_and_go_orb"},
        "pnl_pct": 0.0, "pnl_usd": 0.0,
    }


def test_managed_trade_repository_is_user_scoped_and_logs_stop_moves(db):
    auth = AuthService()
    owner = auth.create_user("managed-owner", "managed owner password")
    other = auth.create_user("managed-other", "managed other password")
    repo = TradeRepository()
    repo.insert(owner.id, _managed_trade("managed-owner-trade"))
    repo.insert(other.id, _managed_trade("managed-other-trade"))

    assert repo.has_any_open(owner.id, "aaa")
    changed = repo.manage(
        owner.id, "AAA", 102.0, now=1_000_100.0, friday_flat=False)

    assert len(changed) == 1
    assert changed[0]["stop"] == 100.02
    assert changed[0]["stop_moves"] == 1
    assert repo.list(other.id)[0]["stop"] == 98.0
    with session_scope() as session:
        events = session.query(TradeEvent).all()
        assert len(events) == 1
        assert events[0].trade_id == "managed-owner-trade"
        assert events[0].kind == "move_stop_breakeven"


def test_managed_vs_fixed_repository_and_algorithm_correctness(db):
    owner = AuthService().create_user("ab-owner", "ab owner password")
    repo = TradeRepository()
    fixed = _managed_trade("fixed", ticker="DDD", trader="bot-gap-fixed", managed=False)
    managed = _managed_trade("managed", ticker="DDD", trader="bot-gap-mgd", managed=True)
    repo.insert(owner.id, fixed)
    repo.insert(owner.id, managed)
    repo.close(owner.id, "fixed", 99.0, "manual", 1_000_100.0)
    repo.close(owner.id, "managed", 104.0, "manual", 1_000_100.0)

    comparison = repo.managed_vs_fixed(owner.id)
    assert comparison["n_pairs"] == 1
    assert comparison["pairs"][0]["delta_pct"] == 5.0
    correctness = repo.algorithm_correctness(owner.id)
    assert correctness["totals"]["n_trades"] == 1
    assert correctness["ideas"][0]["traders"] == ["bot-gap-fixed"]
