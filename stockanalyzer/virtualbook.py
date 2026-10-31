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

Persistence goes through :class:`~stockanalyzer.db.repositories.trades.TradeRepository`
(SQLAlchemy, PostgreSQL in production — see ``DATABASE_URL``). Every function is
scoped to one owner via the keyword-only ``user_id`` and takes an optional
``now`` for deterministic tests.
"""
from __future__ import annotations

import time
import uuid

from .db.repositories.trades import TradeRepository, _agg, trade_stats

STAKE_USD = 1000.0

_REPOSITORY = TradeRepository()


def _owner(user_id: str | None) -> str:
    if not user_id:
        raise ValueError("user_id is required")
    return user_id


def load(*, user_id: str) -> list[dict]:
    return _REPOSITORY.list(_owner(user_id))


def has_open(ticker: str, trader: str, *, user_id: str) -> bool:
    return _REPOSITORY.has_open(_owner(user_id), ticker, trader)


def open_position(*, ticker: str, trader: str, entry: float, stop: float,
                  target: float, kind: str = "immediate",
                  trigger: float | None = None, horizon_days: int = 3,
                  stake: float = STAKE_USD, snapshot: dict | None = None,
                  managed: bool = False, hold_weekend: bool = False,
                  entry_rvol: float | None = None, init_stop: float | None = None,
                  now: float | None = None, user_id: str) -> dict:
    """Open a virtual position (or a pending breakout order).

    ``snapshot`` carries the full decision context — signals, indicators,
    verdict, swing checks, recommendation — stored in normalized DB tables
    for post-hoc analysis. ``managed`` marks a row whose exit is run by
    ``manage`` (the Gap-and-Go A/B); ``init_stop`` pins the original 1R.
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
    _REPOSITORY.insert(_owner(user_id), trade, context=snapshot)
    return trade


def close_position(pid: str, exit_price: float, reason: str = "manual",
                   now: float | None = None, *, user_id: str) -> dict | None:
    return _REPOSITORY.close(_owner(user_id), pid, exit_price, reason, now)


def mark(ticker: str, price: float, now: float | None = None,
         *, user_id: str) -> list[dict]:
    """Mark a ticker's positions to ``price``: activate pending breakout orders,
    auto-close stop/target hits (stop wins on ambiguity), expire stale trades.
    Returns the positions whose status changed (for toasts)."""
    return _REPOSITORY.mark(_owner(user_id), ticker, price, now)


def manage(ticker: str, price: float, reports=None, trend_change=None,
           now: float | None = None, *, user_id: str,
           friday_flat: bool | None = None) -> list[dict]:
    """Run best-practice exit management on this ticker's MANAGED positions
    (breakeven/trail/trend-test-tighten/weekend-flat). Call before ``mark`` so a
    freshly-tightened stop can trigger the close on the same tick.
    ``friday_flat`` overrides the market-calendar check (tests)."""
    return _REPOSITORY.manage(_owner(user_id), ticker, price, reports, trend_change,
                              now, friday_flat=friday_flat)


def has_any_open(ticker: str, *, user_id: str) -> bool:
    """True if any trader holds an open/pending position in this ticker."""
    return _REPOSITORY.has_any_open(_owner(user_id), ticker)


def stats(positions: list[dict] | None = None, *, user_id: str | None = None) -> dict:
    """Per-trader / per-setup / per-score-band aggregates over closed trades.

    Pass ``positions`` (e.g. an already-loaded book) to aggregate in memory;
    otherwise the owner's book is read from the database."""
    if positions is not None:
        return trade_stats(positions)
    return _REPOSITORY.stats(_owner(user_id))


# What each trader's rule is — shown beside its row so the report card reads on
# its own. Unknown traders still get a row (labelled "?"), never dropped.
TRADER_RULES = {
    "me": "manual 📒 Virtual BUY",
    "bot-GO": "swing plan, strict GO only",
    "bot-70": "swing plan, score ≥70 immediate (not GO)",
    "bot-BRK": "swing plan, armed breakout_wait orders",
    "bot-gap-fixed": "Gap-and-Go ORB, fixed stop/target",
    "bot-gap-mgd": "Gap-and-Go ORB, managed exit",
    "rev-trend-touch": "reversal @ support, daily uptrend, first touch",
    "rev-trend-confirm": "reversal @ support, daily uptrend, confirmed reclaim",
    "rev-strong-touch": "reversal @ strong support, any trend, first touch",
    "rev-strong-confirm": "reversal @ strong support, any trend, confirmed reclaim",
}


def _month(ts) -> str:
    try:
        return time.strftime("%Y-%m", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def unrealized_usd(position: dict, price: float | None) -> float | None:
    """Mark-to-market $ of an OPEN position; None when unpriced or not open.

    Pending (armed) orders hold no shares yet, so they carry no unrealized P&L."""
    if position.get("status") != "open" or not price:
        return None
    return round((price - position["entry"]) * position["shares"], 2)


def bot_report(positions: list[dict], prices: dict[str, float] | None = None) -> dict:
    """Complete per-trader picture of the book — every trader, nothing truncated.

    ``rows``: one per trader with closed results (cancelled excluded from
    win/loss, counted separately), live open/armed counts, unrealized $ on
    priced open positions and realized + unrealized total.
    ``months``: realized $ per trader per close month (``YYYY-MM``).
    ``unpriced``: open positions with no fresh price (their unrealized is
    unknown, not zero)."""
    prices = prices or {}
    by_trader: dict[str, list[dict]] = {}
    for p in positions:
        by_trader.setdefault(p["trader"], []).append(p)

    rows: list[dict] = []
    months: dict[str, dict[str, float]] = {}
    unpriced = 0
    for trader, items in sorted(by_trader.items()):
        closed = [p for p in items if p["status"] == "closed"
                  and p.get("close_reason") != "cancelled"]
        cancelled = sum(1 for p in items if p["status"] == "closed"
                        and p.get("close_reason") == "cancelled")
        live = [p for p in items if p["status"] == "open"]
        armed = [p for p in items if p["status"] == "pending"]
        unreal = 0.0
        for p in live:
            u = unrealized_usd(p, prices.get(p["ticker"]))
            if u is None:
                unpriced += 1
            else:
                unreal += u
        agg = _agg(closed)
        last = max((p.get("closed_ts") or 0 for p in closed), default=0)
        rows.append(dict(
            trader=trader, rule=TRADER_RULES.get(trader, "?"),
            closed=agg["n"], wins=agg["wins"], losses=agg["losses"],
            win_rate=agg["win_rate"], avg_pnl_pct=agg["avg_pnl_pct"],
            realized_usd=agg["total_pnl_usd"],
            open=len(live), armed=len(armed), cancelled=cancelled,
            unrealized_usd=round(unreal, 2),
            total_usd=round(agg["total_pnl_usd"] + unreal, 2),
            last_close=_month(last) if last else None,
        ))
        for p in closed:
            m = _month(p.get("closed_ts") or p.get("opened_ts"))
            cell = months.setdefault(trader, {})
            cell[m] = round(cell.get(m, 0.0) + p["pnl_usd"], 2)
    return dict(rows=rows, months=months, unpriced=unpriced)


def algorithm_correctness(*, user_id: str) -> dict:
    """Idea-level report (deduped across bots) — judge the algorithm's calls.

    Collapses every trader that took one plan into a single idea (``cohort_id``),
    so the win rate reflects the engine's decisions rather than how many bots
    copied them. Managed (exit-A/B) rows are excluded."""
    return _REPOSITORY.algorithm_correctness(_owner(user_id))


def managed_vs_fixed(*, user_id: str) -> dict:
    """Paired Gap-and-Go A/B: managed exit vs fixed exit on the same ORB idea.

    Pairs ``bot-gap-mgd`` with ``bot-gap-fixed`` by ``cohort_id`` — the answer to
    "is the bot smarter with the adaptive exit behaviors?" isolated from entry
    quality."""
    return _REPOSITORY.managed_vs_fixed(_owner(user_id))


def losing_trade_patterns(*, user_id: str) -> dict:
    """Most-failed checks, common signals and mean indicators among losers."""
    return _REPOSITORY.losing_trade_patterns(_owner(user_id))


def closed_trades(*, user_id: str) -> list[dict]:
    """Closed, non-cancelled positions (newest first)."""
    return _REPOSITORY.closed_trades(_owner(user_id))
