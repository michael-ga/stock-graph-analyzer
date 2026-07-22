"""`_fallback_quote` — the derived (keyless) quote used when no Finnhub key is set.

Guards the Yahoo-style split during extended hours: the header must surface the
*regular-session day move* (day_change) alongside the smaller extended-hours print
(change), not collapse to only the after-hours wiggle.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from stockanalyzer.data.schema import Timeframe
from stockanalyzer.pipeline import _fallback_quote


def _report(df: pd.DataFrame) -> SimpleNamespace:
    # _fallback_quote only touches .df and .meta, so a light stand-in avoids
    # constructing a full TimeframeReport.
    return SimpleNamespace(df=df, meta={"last_close": float(df["close"].iloc[-1])})


def test_extended_hours_surfaces_day_move_and_extended_move():
    # 1D intraday: regular bars closing 105.00, then after-hours up to 106.00.
    idx = pd.to_datetime([
        "2026-06-19 15:50", "2026-06-19 15:55",   # regular
        "2026-06-19 16:05", "2026-06-19 16:10",   # after-hours
    ])
    intraday = pd.DataFrame({
        "open":   [104.5, 104.8, 105.0, 105.5],
        "high":   [105.2, 105.3, 105.8, 106.1],
        "low":    [104.3, 104.6, 104.9, 105.4],
        "close":  [104.8, 105.0, 105.5, 106.0],
        "volume": [1000, 1200, 0, 0],
    }, index=idx)
    # Daily: prior close 100.00 -> today's regular close 105.00 == +5.00 (+5.00%).
    daily = pd.DataFrame({
        "open":   [99.0, 101.0],
        "high":   [101.0, 106.0],
        "low":    [98.0, 100.5],
        "close":  [100.0, 105.0],
        "volume": [5_000_000, 6_000_000],
    }, index=pd.to_datetime(["2026-06-18", "2026-06-19"]))

    q = _fallback_quote({Timeframe.D1: _report(intraday), Timeframe.M1: _report(daily)})

    assert q is not None
    assert q.session == "after-hours" and q.is_extended
    # Structural facts (the only things the caller sets).
    assert q.price == 106.0                 # extended-hours print
    assert q.regular_close == 105.0         # today's regular close (daily frame)
    assert q.prev_close == 100.0            # prior trading day's close
    # Derived: the day move Yahoo leads with, and the smaller extended wiggle.
    assert q.day_change == 5.0 and q.day_change_pct == 5.0
    assert q.ext_change == 1.0 and q.ext_change_pct == 0.95
    # Back-compat headline change == the day move.
    assert q.change == 5.0 and q.change_pct == 5.0


def test_regular_session_uses_daily_closes_and_no_extended_move():
    # All-regular intraday bars: the day move comes straight from the daily frame
    # and there is no extended (pre/after-hours) component.
    idx = pd.to_datetime(["2026-06-19 10:00", "2026-06-19 10:05", "2026-06-19 10:10"])
    intraday = pd.DataFrame({
        "open":   [104.0, 104.5, 105.0],
        "high":   [104.8, 105.2, 105.6],
        "low":    [103.8, 104.3, 104.7],
        "close":  [104.5, 105.0, 105.4],
        "volume": [1000, 1100, 1200],
    }, index=idx)
    daily = pd.DataFrame({
        "open":   [99.0, 104.0],
        "high":   [101.0, 105.6],
        "low":    [98.0, 103.5],
        "close":  [100.0, 105.0],
        "volume": [5_000_000, 6_000_000],
    }, index=pd.to_datetime(["2026-06-18", "2026-06-19"]))

    q = _fallback_quote({Timeframe.D1: _report(intraday), Timeframe.M1: _report(daily)})

    assert q is not None
    assert q.session == "regular" and not q.is_extended
    assert q.regular_close == 105.0 and q.prev_close == 100.0
    assert q.day_change == 5.0 and q.day_change_pct == 5.0
    assert q.ext_change == 0.0 and q.ext_change_pct == 0.0   # no extended session


def test_pct_from_prev_tracks_a_live_price():
    # The formula both the live header (streaming tick) and radar (last print)
    # use, so they agree by construction.
    q = _fallback_quote({Timeframe.D1: _report(pd.DataFrame({
        "open": [104.0, 104.5], "high": [105.0, 105.2], "low": [103.5, 104.0],
        "close": [104.5, 105.0], "volume": [1000, 1100],
    }, index=pd.to_datetime(["2026-06-19 10:00", "2026-06-19 10:05"]))),
        Timeframe.M1: _report(pd.DataFrame({
            "open": [99.0, 104.0], "high": [101.0, 105.2], "low": [98.0, 103.5],
            "close": [100.0, 105.0], "volume": [5_000_000, 6_000_000],
        }, index=pd.to_datetime(["2026-06-18", "2026-06-19"])))})
    assert q is not None
    assert q.pct_from_prev(110.0) == 10.0        # 110 vs prev close 100
    assert q.pct_from_prev() == q.day_change_pct  # default = latest print
