"""Gap-and-Go opening-range-breakout (ORB) decision — pure and testable.

Carter-style intraday rules, extracted verbatim from the live radar so the app
and the backtest share ONE code path (no drift). Everything here is a pure
function of the data plus an explicit as-of timestamp — no Streamlit state, no
``now()`` — so it can be replayed bar-by-bar in ``daytrade_backtest.py`` and
unit-tested.

- ``opening_range`` — the first-15-minute (09:30–09:45 ET) high/low + the gap vs
  the prior close. Available only once that window has completed.
- ``gap_and_go_signal`` — given the opening range and the current price, decide
  whether an ORB entry fires, returning every check's outcome (not just a bool)
  so callers can explain *why* it did or didn't.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..data.market_session import _to_et
from ..session import is_orb_window, market_phase
from .daycard import _session_bars
from .indicators import relative_volume

# Thresholds (single source of truth — app.py imports these).
GAP_MIN_PCT       = 0.02      # ORB only for a real gap-up: open > prev_close +2%
OPENING_RANGE_MIN = 15        # opening range = first 15 minutes (9:30–9:45 ET)
MIN_BREAKOUT_RVOL = 1.5       # breakout needs volume > 1.5× average
ORB_STOP_BUFFER   = 0.001     # hard stop = OR-Low × (1 − 0.1%)
ORB_TARGET_R      = 2.0       # target = entry + 2R

_OR_START_HHMM = "09:30"
_OR_END_HHMM   = "09:45"


@dataclass(frozen=True)
class OpeningRange:
    day: str                   # ET trading day, YYYY-MM-DD
    open: float                # session open (first regular bar)
    high: float                # highest high of 09:30–09:45
    low: float                 # lowest low of 09:30–09:45
    gap_pct: float | None      # open vs prior close (fraction)
    gap_up: bool               # gap_pct >= GAP_MIN_PCT

    def as_dict(self) -> dict:
        """The dict shape the live radar has always passed around."""
        return {"day": self.day, "open": self.open, "high": self.high,
                "low": self.low, "gap_pct": self.gap_pct, "gap_up": self.gap_up}


@dataclass(frozen=True)
class GapGoDecision:
    fired: bool
    reason: str                # why it did / didn't fire (plain English)
    gap_up: bool
    in_window: bool            # inside the 09:45–11:30 ET entry window
    broke_or_high: bool        # price >= opening-range high
    rvol: float | None
    vol_ok: bool               # rvol > MIN_BREAKOUT_RVOL (or unknown)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    rr: float | None = None


def opening_range(intraday_df: pd.DataFrame | None, prev_close: float | None,
                  asof_ts) -> OpeningRange | None:
    """The 09:30–09:45 ET opening range for the session in ``intraday_df``.

    Returns ``None`` until that window has completed (``asof_ts`` still inside the
    opening range / pre-market / closed), or when the intraday data is missing.
    ``prev_close`` is the prior regular close used for the gap measurement.
    """
    if (intraday_df is None or not isinstance(intraday_df.index, pd.DatetimeIndex)
            or intraday_df.empty):
        return None
    # The range isn't real until the first 15 minutes are done.
    if market_phase(asof_ts) in ("opening_range", "premarket", "closed"):
        return None
    sess = _session_bars(intraday_df)
    if sess.empty:
        return None
    hhmm = [_to_et(t).strftime("%H:%M") for t in sess.index]
    win = sess[[_OR_START_HHMM <= t < _OR_END_HHMM for t in hhmm]]
    if win.empty:
        return None
    today_open = float(sess["open"].iloc[0])
    gap_pct = (today_open / prev_close - 1) if (prev_close and today_open) else None
    return OpeningRange(
        day=_to_et(sess.index[-1]).strftime("%Y-%m-%d"),
        open=today_open,
        high=float(win["high"].max()),
        low=float(win["low"].min()),
        gap_pct=gap_pct,
        gap_up=gap_pct is not None and gap_pct >= GAP_MIN_PCT,
    )


def gap_and_go_signal(orange: OpeningRange | None, intraday_df: pd.DataFrame | None,
                      price: float | None, asof_ts) -> GapGoDecision:
    """Decide whether a Gap-and-Go ORB entry fires right now.

    Mirrors the live radar's guard order exactly: real gap-up → inside the
    09:45–11:30 window → price breaking the opening-range HIGH → on > 1.5× volume
    → non-degenerate geometry. The hard stop sits just below the opening-range
    LOW; the target is entry + 2R.
    """
    gap_up = bool(orange is not None and orange.gap_up)
    in_window = bool(is_orb_window(asof_ts))
    broke = bool(orange is not None and price is not None and price >= orange.high)
    rvol = relative_volume(intraday_df) if intraday_df is not None else None
    vol_ok = (rvol is None) or (rvol > MIN_BREAKOUT_RVOL)

    def _no(reason: str) -> GapGoDecision:
        return GapGoDecision(False, reason, gap_up, in_window, broke, rvol, vol_ok)

    if orange is None or not gap_up:
        return _no(f"no gap-up ≥ {GAP_MIN_PCT * 100:.0f}%")
    if price is None:
        return _no("no live price")
    if not in_window:
        return _no("outside the 09:45–11:30 ET entry window")
    if not broke:
        return _no(f"price ${price:.2f} still below OR-high ${orange.high:.2f}")
    if not vol_ok:
        return _no(f"break on {rvol:.1f}×vol (need > {MIN_BREAKOUT_RVOL:g}×)")

    entry = float(price)
    stop = round(orange.low * (1 - ORB_STOP_BUFFER), 4)
    if stop >= entry:
        return _no("degenerate geometry — stop ≥ entry")
    target = round(entry + ORB_TARGET_R * (entry - stop), 4)
    rr = round((target - entry) / (entry - stop), 2) if entry > stop else ORB_TARGET_R
    return GapGoDecision(
        True, f"ORB break of ${orange.high:.2f} on {(rvol or 0):.1f}×vol",
        gap_up, in_window, broke, rvol, vol_ok,
        entry=entry, stop=stop, target=target, rr=rr)
