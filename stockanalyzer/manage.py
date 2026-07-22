"""Best-practice exit management for the managed Gap-and-Go bot (``bot-gap-mgd``).

Pure and testable: ``assess_position`` looks at an open position plus the live
picture and returns the management actions to apply. Two rules that the user
asked for, made disciplined:

  1. **Cautious protective stop** — move to (spread-adjusted) breakeven at +1R,
     then trail behind the tighter of a 3×ATR chandelier and the 5-min EMA8.
     **Ratchet-only: a stop is NEVER widened.**
  2. **Smart trend-flip handling (test, don't dump)** — a trend flip *or* a
     5-min close below EMA8 *tightens* the stop to "test" the move; it never
     market-dumps a winner on one noisy bar. Only a real break of the tightened
     stop closes the trade.

Plus a weekend safeguard: day-trades are flattened into the Friday close unless
explicitly flagged to hold.

This module owns no DB or Streamlit state — ``data.store.manage_trades`` applies
what it returns.
"""
from __future__ import annotations

from dataclasses import dataclass

from .analysis.daycard import _intraday_frame, _session_bars
from .analysis.signals import Direction

# ── tunable best-practice constants (pinned by tests) ───────────────────────── #
_BE_TRIGGER_R     = 1.0      # move stop to breakeven at +1R
_TRAIL_START_R    = 1.5      # begin trailing once >= +1.5R
_TRAIL_ATR_MULT   = 3.0      # chandelier: highest session close - 3*ATR
_EMA8_BUFFER      = 0.001    # trail just under the 5-min EMA8 (0.1%)
_TREND_FLIP_CONF  = 0.6      # trend-change score needed to "test" a flip
_TEST_ATR_MULT    = 0.6      # trend-test tighten: stop = price - 0.6*ATR
_SPREAD_PER_SHARE = 0.02     # flat spread/commission per share → breakeven = entry + this

_DEFAULT_ATR_FRAC = 0.02     # fallback daily volatility when ATR is unavailable


@dataclass(frozen=True)
class ManageAction:
    kind: str            # move_stop_breakeven | trail_stop | trend_test_tighten | weekend_flat
    severity: str        # good | warn | bad | info
    reason: str
    new_stop: float | None = None
    fraction: float | None = None     # 1.0 = flatten the position
    r_multiple: float | None = None


def breakeven_stop(entry: float) -> float:
    """Breakeven that nets ~flat AFTER the spread (entry plus the flat per-share fee)."""
    return round(entry + _SPREAD_PER_SHARE, 4)


# ── intraday readouts (shared with store.manage_trades) ─────────────────────── #

def intraday_readout(reports) -> tuple[float | None, float | None]:
    """(ema8, last_close) from the shortest intraday frame, or (None, None)."""
    df = _intraday_frame(reports) if reports else None
    if df is None or df.empty:
        return None, None
    ema8 = None
    if "ema8" in df:
        s = df["ema8"].dropna()
        ema8 = float(s.iloc[-1]) if not s.empty else None
    close = float(df["close"].iloc[-1]) if len(df) else None
    return ema8, close


def _atr_frac(reports) -> float:
    df = _intraday_frame(reports) if reports else None
    if df is None or df.empty or "atr" not in df:
        return _DEFAULT_ATR_FRAC
    atr = df["atr"].dropna()
    close = df["close"].dropna()
    if atr.empty or close.empty or close.iloc[-1] <= 0:
        return _DEFAULT_ATR_FRAC
    return float(atr.iloc[-1] / close.iloc[-1])


def _chandelier_stop(reports, mult: float) -> float | None:
    """Highest session close − mult×ATR (the classic chandelier trail)."""
    df = _intraday_frame(reports) if reports else None
    if df is None or df.empty or "atr" not in df:
        return None
    atr = df["atr"].dropna()
    if atr.empty:
        return None
    sess = _session_bars(df)
    closes = sess["close"] if len(sess) else df["close"]
    highest = float(closes.max())
    return round(highest - mult * float(atr.iloc[-1]), 4)


# ── the assessment ──────────────────────────────────────────────────────────── #

def assess_position(position: dict, price: float, reports, verdict,
                    trend_change, now: float,
                    ema8_5m: float | None = None,
                    intraday_close: float | None = None,
                    is_friday_flat: bool = False,
                    hold_weekend: bool = False) -> list[ManageAction]:
    """Return the management actions for one open (long) position.

    ``verdict`` is accepted for API stability / future use; the decision uses
    ``trend_change`` (a ``TrendChange``), the 5-min EMA8 and the R-multiple.
    """
    entry = float(position["entry"])
    init_stop = float(position.get("init_stop") or position["stop"])
    cur_stop = float(position["stop"])
    risk = max(1e-9, entry - init_stop)               # 1R in price terms
    r_now = (price - entry) / risk
    atr_frac = _atr_frac(reports)
    out: list[ManageAction] = []

    # 0) Weekend safeguard — flatten day-trades into the Friday close.
    if is_friday_flat and not hold_weekend:
        return [ManageAction("weekend_flat", "warn",
                "Friday close — flattening the day-trade to dodge the weekend gap.",
                fraction=1.0, r_multiple=round(r_now, 2))]

    # 1) Trend-flip / EMA8 break: TEST, don't dump. Tighten under the bar and let
    #    the market confirm — a noisy bar can't flush a winner, only a real break.
    flip = bool(trend_change is not None and getattr(trend_change, "likely", False)
                and trend_change.score >= _TREND_FLIP_CONF
                and trend_change.direction == Direction.BEAR)
    below_ema8 = (ema8_5m is not None and intraday_close is not None
                  and intraday_close < ema8_5m)
    if flip or below_ema8:
        test_stop = max(cur_stop, round(price * (1 - _TEST_ATR_MULT * atr_frac), 4))
        if test_stop > cur_stop:
            why = ("5-min closed below EMA8" if below_ema8
                   else f"trend flipping (conf {trend_change.score:.2f})")
            out.append(ManageAction("trend_test_tighten", "warn",
                f"{why} — tightening to test the move, not dumping.",
                new_stop=test_stop, r_multiple=round(r_now, 2)))

    # 2) Dynamic trail once the trade is paying well: ride the tighter of the
    #    5-min EMA8 and the 3×ATR chandelier. Ratchet-only.
    if r_now >= _TRAIL_START_R:
        cands = [cur_stop]
        chand = _chandelier_stop(reports, _TRAIL_ATR_MULT)
        if chand is not None:
            cands.append(chand)
        if ema8_5m is not None:
            cands.append(round(ema8_5m * (1 - _EMA8_BUFFER), 4))
        trail = max(cands)
        if trail > cur_stop:
            out.append(ManageAction("trail_stop", "info",
                "Trailing under the 5-min EMA8 / 3×ATR — locking in the win.",
                new_stop=trail, r_multiple=round(r_now, 2)))
    # 3) Spread-adjusted breakeven once the trade reaches +1R — no localized loss.
    elif r_now >= _BE_TRIGGER_R and cur_stop < breakeven_stop(entry):
        out.append(ManageAction("move_stop_breakeven", "good",
            "+1R — stop to spread-adjusted breakeven; the trade can't lose now.",
            new_stop=breakeven_stop(entry), r_multiple=round(r_now, 2)))

    return out
