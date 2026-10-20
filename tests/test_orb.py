"""Gap-and-Go opening-range-breakout decision (stockanalyzer.analysis.orb).

These pin the pure ORB logic the live radar and daytrade_backtest.py both call:
the opening range / gap measurement, and the fire/no-fire decision with its
guard order (gap → window → OR-high break → volume → geometry).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis import orb

# 2026-06-19 is a Friday (a weekday, so the ORB window is live). Naive timestamps
# are treated as Eastern by the session helpers.
_DAY = "2026-06-19"


def _session(break_close: float = 104.6, break_vol: float = 6000.0,
             day_open: float = 103.0) -> pd.DataFrame:
    """A 25-bar 5-min session (09:30–11:30). Opening range = 102.5–104.0; every bar
    stays below 104.0 until a single break bar at 11:05."""
    idx = pd.date_range(f"{_DAY} 09:30", periods=25, freq="5min")
    n = len(idx)
    close = np.full(n, 103.0)
    high = np.full(n, 103.6)
    low = np.full(n, 103.0)
    vol = np.full(n, 1000.0)
    # Opening range (09:30, 09:35, 09:40) sets high 104.0 / low 102.5.
    close[0], high[0], low[0] = day_open, 103.6, 102.6
    high[1], low[1] = 104.0, 103.0          # OR high
    high[2], low[2] = 103.9, 102.5          # OR low
    # The break bar at 11:05 (index 19 → 20 bars of history for RVOL).
    b = 19
    close[b], high[b], low[b], vol[b] = break_close, break_close + 0.2, 103.5, break_vol
    df = pd.DataFrame({"open": close, "high": high, "low": low,
                       "close": close, "volume": vol}, index=idx)
    return df, idx[b]


def test_opening_range_gap_and_levels():
    df, _ = _session(day_open=103.0)
    orng = orb.opening_range(df, prev_close=100.0, asof_ts=pd.Timestamp(f"{_DAY} 11:00"))
    assert orng is not None
    assert orng.high == 104.0 and orng.low == 102.5
    assert orng.open == 103.0
    assert round(orng.gap_pct, 4) == 0.03          # 103 vs 100
    assert orng.gap_up is True                     # 3% ≥ 2%


def test_opening_range_none_until_window_completes():
    df, _ = _session()
    # 09:40 is still inside the opening range → not yet available.
    assert orb.opening_range(df, 100.0, pd.Timestamp(f"{_DAY} 09:40")) is None


def test_gap_and_go_fires_on_volume_break():
    df, brk = _session(break_close=104.6, break_vol=6000.0)
    orng = orb.opening_range(df, 100.0, pd.Timestamp(f"{_DAY} 11:00"))
    upto = df[df.index <= brk]
    dec = orb.gap_and_go_signal(orng, upto, price=float(upto["close"].iloc[-1]), asof_ts=brk)
    assert dec.fired is True
    assert dec.gap_up and dec.in_window and dec.broke_or_high and dec.vol_ok
    assert dec.entry == 104.6
    assert dec.stop == round(102.5 * (1 - orb.ORB_STOP_BUFFER), 4)
    assert dec.rr == orb.ORB_TARGET_R              # target set at 2R by construction


def test_gap_and_go_blocked_without_volume():
    df, brk = _session(break_close=104.6, break_vol=800.0)   # break on shrinking volume
    orng = orb.opening_range(df, 100.0, pd.Timestamp(f"{_DAY} 11:00"))
    upto = df[df.index <= brk]
    dec = orb.gap_and_go_signal(orng, upto, price=float(upto["close"].iloc[-1]), asof_ts=brk)
    assert dec.fired is False
    assert dec.broke_or_high and not dec.vol_ok
    assert "vol" in dec.reason.lower()


def test_gap_and_go_no_fire_without_gap():
    df, brk = _session(day_open=100.5)              # open 100.5 vs prev 100 → +0.5% gap
    orng = orb.opening_range(df, 100.0, pd.Timestamp(f"{_DAY} 11:00"))
    assert orng.gap_up is False
    dec = orb.gap_and_go_signal(orng, df[df.index <= brk], price=104.6, asof_ts=brk)
    assert dec.fired is False and not dec.gap_up
    assert "gap" in dec.reason.lower()


def test_gap_and_go_no_fire_outside_window():
    df, _ = _session()
    orng = orb.opening_range(df, 100.0, pd.Timestamp(f"{_DAY} 11:00"))
    late = pd.Timestamp(f"{_DAY} 13:00")            # after the 11:30 cutoff
    dec = orb.gap_and_go_signal(orng, df, price=104.6, asof_ts=late)
    assert dec.fired is False and not dec.in_window
    assert "window" in dec.reason.lower()


def test_gap_is_measured_from_the_regular_open_not_premarket():
    """SNDK 2026-10-16 shape: pre-market opened 1380 (−1.1% vs 1394.83), the 09:30
    open was 1348.82 (−3.3%). The gap must come from 09:30, else a real gap reads
    as nothing (2026-10-21 snapshot review, L1)."""
    pre_idx = pd.date_range(f"{_DAY} 04:00", f"{_DAY} 09:25", freq="5min")
    pre = pd.DataFrame({"open": 1380.0, "high": 1381.0, "low": 1350.0,
                        "close": 1355.0, "volume": 0.0}, index=pre_idx)
    reg, _ = _session()                                   # 09:30 open = 103.0
    reg[["open", "high", "low", "close"]] += 1245.82      # shift to SNDK prices: open 1348.82
    df = pd.concat([pre, reg])
    orng = orb.opening_range(df, prev_close=1394.83, asof_ts=pd.Timestamp(f"{_DAY} 11:00"))
    assert orng.open == 1348.82
    assert round(orng.gap_pct, 4) == round(1348.82 / 1394.83 - 1, 4)   # ≈ −3.3%, not −1.1%


def test_premarket_gap_up_that_opens_flat_is_not_a_gap_up():
    """+3% in pre-market but a flat 09:30 open is not a Gap-and-Go candidate."""
    pre = pd.DataFrame({"open": 103.0, "high": 103.2, "low": 102.8, "close": 103.0,
                        "volume": 0.0}, index=pd.date_range(f"{_DAY} 07:00", periods=6, freq="5min"))
    reg, _ = _session(day_open=100.2)
    orng = orb.opening_range(pd.concat([pre, reg]), 100.0, pd.Timestamp(f"{_DAY} 11:00"))
    assert orng.gap_up is False
