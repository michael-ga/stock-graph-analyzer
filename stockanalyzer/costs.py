"""Trading-cost arithmetic for reports: one cost basis for bots and setups.

Measurement only. Nothing here is read by a decision, a gate or a bot; the replay
A/B report and ``algolab/analyze.py`` use it to put every bot and setup on the same
footing (see ``docs/plans/2026-11-10-daytrade-optimization-plan.md``, F3 and F6).

Two views:
- **R basis.** A round trip costs ``max($0.02/share, 10 bps of price)``. The flat
  $0.02 is what the book and the setups harness already charge; the 10 bps floor
  covers spread and slippage on the higher-priced names, where $0.02 is close to
  nothing. Expressed in R it is ``cost / (entry - stop)``.
- **Account basis.** The user's own trades are a fixed dollar stake with a fixed
  dollar fee ($1.50 + $1.50). What a setup earns per trade is its average gross %
  move times the stake, minus the fee.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

PER_SHARE = 0.02          # flat round-trip $/share (the book's _SPREAD_PER_SHARE)
BPS = 10.0                # round-trip floor in basis points of price
ACCOUNT_FEE = 3.0         # the user's broker fee per round trip, $
ACCOUNT_STAKES = (300.0, 500.0, 1000.0)


def round_trip_cost(entry: float) -> float:
    """$/share for one round trip at ``entry``."""
    return max(PER_SHARE, float(entry) * BPS / 1e4)


def cost_r(entry: float | None, stop: float | None) -> float | None:
    """The round-trip cost in R for a long with this entry and stop, or None when the
    geometry is unknown or degenerate."""
    if entry is None or stop is None:
        return None
    risk = float(entry) - float(stop)
    if risk <= 0:
        return None
    return round_trip_cost(entry) / risk


def risk_pct(entry: float | None, stop: float | None) -> float | None:
    """Stop distance in % of entry, or None."""
    if entry is None or stop is None or float(entry) <= 0 or float(entry) <= float(stop):
        return None
    return (float(entry) - float(stop)) / float(entry) * 100


def net_dollars(avg_gross_pct: float, stake: float, fee: float = ACCOUNT_FEE) -> float:
    """What one average trade leaves on ``stake`` after the fixed fee."""
    return stake * avg_gross_pct / 100 - fee


def breakeven_stake(avg_gross_pct: float, fee: float = ACCOUNT_FEE) -> float:
    """The stake at which the average trade pays the fixed fee (inf if no edge)."""
    return fee / (avg_gross_pct / 100) if avg_gross_pct > 0 else math.inf


def account_view(gross_pcts: Iterable[float], stakes=ACCOUNT_STAKES,
                 fee: float = ACCOUNT_FEE) -> dict:
    """``{n, avg_gross_pct, net: {stake: $}, breakeven_stake}`` for a list of
    per-trade gross % moves (exit / entry − 1, in %)."""
    xs = [float(x) for x in gross_pcts if x is not None and math.isfinite(float(x))]
    if not xs:
        return {"n": 0, "avg_gross_pct": None, "net": {}, "breakeven_stake": None}
    avg = sum(xs) / len(xs)
    return {"n": len(xs), "avg_gross_pct": avg,
            "net": {s: net_dollars(avg, s, fee) for s in stakes},
            "breakeven_stake": breakeven_stake(avg, fee)}
