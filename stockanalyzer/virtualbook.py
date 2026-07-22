"""Virtual paper-trading book.

Every position is virtual ($1,000 stake each) but tracked against real prices:
manual buys ("me") and strategy bots trade side by side, and every close is
stored with the full prediction snapshot (score, setup, kind, failed checks,
signals, indicators, verdict…) so the algorithm can be judged and improved
from evidence.

Position lifecycle:
    pending  — armed breakout order; activates when price crosses `trigger`
    open     — live position; auto-closes at stop (conservative) or target,
               or expires at market after ~1.5× the horizon in calendar days
    closed   — final; carries exit price, reason and realized P&L

All functions are DB-backed (``trades.db``) and take an optional ``now``
for deterministic tests.  Legacy JSON data is auto-migrated on first run.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from .db.repositories.trades import TradeRepository

from .data.store import (
    DB_PATH,
    algorithm_correctness as _algorithm_correctness,
    close_trade,
    has_any_open as _has_any_open,
    has_open_trade,
    insert_trade,
    load_trades,
    manage_trades,
    managed_vs_fixed as _managed_vs_fixed,
    mark_trades,
    trade_stats,
)

STAKE_USD = 1000.0

# Legacy path kept only so the JSON→SQLite migration can find the old file.
_PATH = Path(__file__).resolve().parent.parent / ".virtualbook.json"
_REPOSITORY = TradeRepository()


def _postgres(path: Path, user_id: str | None) -> bool:
    if path != _PATH or not os.getenv("DATABASE_URL"):
        return False
    if not user_id:
        raise ValueError("user_id is required for PostgreSQL persistence")
    return True


def _db(path: Path) -> Path:
    """Resolve a caller-supplied book path to its SQLite DB.

    The default path maps to the global ``trades.db``; any other path (tests)
    gets its own isolated DB file alongside it."""
    if path == _PATH:
        return DB_PATH
    path = Path(path)
    return path if path.suffix == ".db" else path.with_suffix(".db")


def load(path: Path = _PATH, *, user_id: str | None = None) -> list[dict]:
    if _postgres(path, user_id):
        return _REPOSITORY.list(user_id)
    return load_trades(_db(path))


def has_open(ticker: str, trader: str, path: Path = _PATH,
             *, user_id: str | None = None) -> bool:
    if _postgres(path, user_id):
        return _REPOSITORY.has_open(user_id, ticker, trader)
    return has_open_trade(ticker.upper(), trader, db_path=_db(path))


def open_position(*, ticker: str, trader: str, entry: float, stop: float,
                  target: float, kind: str = "immediate",
                  trigger: float | None = None, horizon_days: int = 3,
                  stake: float = STAKE_USD, snapshot: dict | None = None,
                  managed: bool = False, hold_weekend: bool = False,
                  entry_rvol: float | None = None, init_stop: float | None = None,
                  now: float | None = None, path: Path = _PATH,
                  user_id: str | None = None) -> dict:
    """Open a virtual position (or a pending breakout order).

    ``snapshot`` carries the full decision context — signals, indicators,
    verdict, swing checks, recommendation — stored in normalized DB tables
    for post-hoc analysis. ``managed`` marks a row whose exit is run by
    ``manage_trades`` (the Gap-and-Go A/B); ``init_stop`` pins the original 1R.
    """
    now = now or time.time()
    status = "pending" if (kind == "breakout_wait" and trigger) else "open"
    stake = float(stake) if stake and stake > 0 else STAKE_USD
    trade = dict(
        id=uuid.uuid4().hex[:10], ticker=ticker.upper(), trader=trader,
        status=status, kind=kind,
        opened_ts=now, opened=time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        activated_ts=(None if status == "pending" else now),
        entry=round(entry, 4), stop=round(stop, 4), target=round(target, 4),
        trigger=(round(trigger, 4) if trigger else None),
        stake=round(stake, 2),
        shares=round(stake / entry, 4) if entry else 0.0,
        horizon_days=int(horizon_days),
        managed=bool(managed), hold_weekend=bool(hold_weekend),
        entry_rvol=(round(float(entry_rvol), 3) if entry_rvol is not None else None),
        init_stop=round(float(init_stop if init_stop is not None else stop), 4),
        snapshot=snapshot or {},
        exit_price=None, close_reason=None, closed=None,
        pnl_pct=0.0, pnl_usd=0.0,
    )
    if _postgres(path, user_id):
        _REPOSITORY.insert(user_id, trade, context=snapshot)
    else:
        insert_trade(trade, context=snapshot, db_path=_db(path))
    return trade


def close_position(pid: str, exit_price: float, reason: str = "manual",
                   now: float | None = None, path: Path = _PATH,
                   *, user_id: str | None = None) -> dict | None:
    if _postgres(path, user_id):
        return _REPOSITORY.close(user_id, pid, exit_price, reason, now)
    return close_trade(pid, exit_price, reason, now, db_path=_db(path))


def mark(ticker: str, price: float, now: float | None = None,
         path: Path = _PATH, *, user_id: str | None = None) -> list[dict]:
    """Mark a ticker's positions to ``price``: activate pending breakout orders,
    auto-close stop/target hits (stop wins on ambiguity), expire stale trades.
    Returns the positions whose status changed (for toasts)."""
    if _postgres(path, user_id):
        return _REPOSITORY.mark(user_id, ticker, price, now)
    return mark_trades(ticker, price, now, db_path=_db(path))


def manage(ticker: str, price: float, reports=None, trend_change=None,
           now: float | None = None, path: Path = _PATH,
           *, user_id: str | None = None) -> list[dict]:
    """Run best-practice exit management on this ticker's MANAGED positions
    (breakeven/trail/trend-test-tighten/weekend-flat). Call before ``mark`` so a
    freshly-tightened stop can trigger the close on the same tick."""
    if _postgres(path, user_id):
        return _REPOSITORY.manage(user_id, ticker, price, reports, trend_change, now)
    return manage_trades(ticker, price, reports, trend_change, now, db_path=_db(path))


def has_any_open(ticker: str, path: Path = _PATH,
                 *, user_id: str | None = None) -> bool:
    """True if any trader holds an open/pending position in this ticker."""
    if _postgres(path, user_id):
        return _REPOSITORY.has_any_open(user_id, ticker)
    return _has_any_open(ticker.upper(), db_path=_db(path))


def stats(positions: list[dict] | None = None, *, user_id: str | None = None) -> dict:
    """Per-trader / per-setup / per-score-band aggregates over closed trades."""
    if positions is not None:
        from .data.store import _agg, _band
        closed = [p for p in positions
                  if p["status"] == "closed" and p.get("close_reason") != "cancelled"]
        by_trader: dict = {}
        by_setup: dict = {}
        by_band: dict = {}
        for p in closed:
            by_trader.setdefault(p["trader"], []).append(p)
            by_setup.setdefault(p.get("snapshot", {}).get("setup", "?"), []).append(p)
            by_band.setdefault(_band(p.get("snapshot", {}).get("score")), []).append(p)
        return dict(
            totals=_agg(closed),
            traders={k: _agg(v) for k, v in sorted(by_trader.items())},
            setups={k: _agg(v) for k, v in sorted(by_setup.items())},
            bands={k: _agg(v) for k, v in sorted(by_band.items())},
        )
    if os.getenv("DATABASE_URL"):
        if not user_id:
            raise ValueError("user_id is required for PostgreSQL persistence")
        return _REPOSITORY.stats(user_id)
    return trade_stats()


def algorithm_correctness(path: Path = _PATH, *, user_id: str | None = None) -> dict:
    """Idea-level report (deduped across bots) — judge the algorithm's calls.

    See ``store.algorithm_correctness``: collapses every trader that took one
    plan into a single idea, so the win rate reflects the engine's decisions
    rather than how many bots copied them."""
    if _postgres(path, user_id):
        return _REPOSITORY.algorithm_correctness(user_id)
    return _algorithm_correctness(_db(path))


def managed_vs_fixed(path: Path = _PATH, *, user_id: str | None = None) -> dict:
    """Paired Gap-and-Go A/B: managed exit vs fixed exit on the same ORB idea.

    See ``store.managed_vs_fixed`` — the answer to "is the bot smarter with the
    adaptive exit behaviors?" isolated from entry quality."""
    if _postgres(path, user_id):
        return _REPOSITORY.managed_vs_fixed(user_id)
    return _managed_vs_fixed(_db(path))
