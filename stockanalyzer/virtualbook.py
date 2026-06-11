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

import time
import uuid
from pathlib import Path

from .data.store import (
    DB_PATH,
    close_trade,
    has_open_trade,
    insert_trade,
    load_trades,
    mark_trades,
    trade_stats,
)

STAKE_USD = 1000.0

# Legacy path kept only so the JSON→SQLite migration can find the old file.
_PATH = Path(__file__).resolve().parent.parent / ".virtualbook.json"


def load(path: Path = _PATH) -> list[dict]:
    return load_trades()


def has_open(ticker: str, trader: str, path: Path = _PATH) -> bool:
    return has_open_trade(ticker.upper(), trader)


def open_position(*, ticker: str, trader: str, entry: float, stop: float,
                  target: float, kind: str = "immediate",
                  trigger: float | None = None, horizon_days: int = 3,
                  stake: float = STAKE_USD, snapshot: dict | None = None,
                  now: float | None = None, path: Path = _PATH) -> dict:
    """Open a virtual position (or a pending breakout order).

    ``snapshot`` carries the full decision context — signals, indicators,
    verdict, swing checks, recommendation — stored in normalized DB tables
    for post-hoc analysis.
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
        snapshot=snapshot or {},
        exit_price=None, close_reason=None, closed=None,
        pnl_pct=0.0, pnl_usd=0.0,
    )
    insert_trade(trade, context=snapshot)
    return trade


def close_position(pid: str, exit_price: float, reason: str = "manual",
                   now: float | None = None, path: Path = _PATH) -> dict | None:
    return close_trade(pid, exit_price, reason, now)


def mark(ticker: str, price: float, now: float | None = None,
         path: Path = _PATH) -> list[dict]:
    """Mark a ticker's positions to ``price``: activate pending breakout orders,
    auto-close stop/target hits (stop wins on ambiguity), expire stale trades.
    Returns the positions whose status changed (for toasts)."""
    return mark_trades(ticker, price, now)


def stats(positions: list[dict] | None = None) -> dict:
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
    return trade_stats()
