"""The contract every context-based intraday setup implements.

A setup is a pure as-of decision: given today's regular-session bars up to and
including the bar that just closed, plus the prior-day context, does it fire,
and with what entry / stop / target? No I/O, no ``now()``, no Streamlit, so the
backtest harness can step it bar by bar and a test can prove it never peeks
ahead (see ``tests/test_setups.py``).

A setup module exposes:
- ``NAME`` (str) and a frozen config dataclass with the knobs it sweeps over;
- ``VARIANTS`` — the config rows the harness (and any future bot) iterates,
  the same "add a row, everything picks it up" rule as ``reversal.REVERSAL_VARIANTS``;
- ``signal(ctx: SetupContext, cfg) -> SetupDecision``.

``fired`` means "a position would be open now": either at the as-of bar's close
(``filled_intrabar=False``) or at a resting order's price that the as-of bar
traded through (``filled_intrabar=True``; the judge then checks that same bar's
low against the stop, stop-first).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

STOP_BUFFER = 0.001        # hard stop sits 0.1% beyond the invalidation level
MIN_RR      = 1.0          # skip any trade whose target is < 1R away


@dataclass(frozen=True)
class SetupContext:
    session: pd.DataFrame                 # today's 09:30.. ET bars, as-of bar last
    prev_session: pd.DataFrame | None     # the previous regular session's bars
    premarket: pd.DataFrame | None        # today's 04:00–09:25 ET bars
    prev_close: float | None
    daily: pd.DataFrame | None            # daily bars strictly before today

    @property
    def asof(self) -> pd.Timestamp:
        return self.session.index[-1]


@dataclass(frozen=True)
class SetupDecision:
    fired: bool
    reason: str                           # why it did / didn't fire (plain English)
    setup: str
    variant: str
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    rr: float | None = None
    filled_intrabar: bool = False


def no(cfg, setup: str, reason: str) -> SetupDecision:
    return SetupDecision(False, reason, setup, cfg.name)


def fire(cfg, setup: str, reason: str, entry: float, stop: float, target: float,
         *, filled_intrabar: bool = False) -> SetupDecision:
    """A fired decision, or a refusal when the geometry is degenerate / < MIN_RR."""
    if stop >= entry:
        return no(cfg, setup, "degenerate geometry — stop ≥ entry")
    rr = (target - entry) / (entry - stop)
    if rr < MIN_RR - 1e-9:                 # tolerance: an exact 1R target is 1R
        return no(cfg, setup, f"target only {rr:.2f}R away (need ≥ {MIN_RR:g}R)")
    return SetupDecision(True, reason, setup, cfg.name, entry=round(entry, 4),
                         stop=round(stop, 4), target=round(target, 4),
                         rr=round(rr, 2), filled_intrabar=filled_intrabar)


def hhmm(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(index.strftime("%H:%M"))


def bar_ranges(df: pd.DataFrame) -> np.ndarray:
    return (df["high"] - df["low"]).to_numpy(dtype=float)


def daily_bias_up(daily: pd.DataFrame | None, sma: int = 20) -> bool | None:
    """Last daily close above its ``sma``-day mean; None without enough history."""
    if daily is None or len(daily) < sma:
        return None
    closes = daily["close"].astype(float)
    return bool(closes.iloc[-1] > closes.iloc[-sma:].mean())
