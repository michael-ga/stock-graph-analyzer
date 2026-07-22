"""Candle-interval roll-up: 5-minute bars → 15m / 30m views.

The chart derives coarser candles locally instead of re-fetching, so the
aggregation has to be exactly right — a mis-binned open or a phantom overnight
candle would show the user a bar that never traded.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalyzer.data.resample import (available_intervals,
                                         infer_interval_minutes,
                                         resample_ohlcv)


def _intraday(n: int = 12, start: str = "2026-06-15 09:30", freq: str = "5min"):
    """n consecutive 5-minute bars with distinguishable OHLCV per bar."""
    idx = pd.date_range(start, periods=n, freq=freq)
    i = np.arange(n, dtype=float)
    return pd.DataFrame(
        {"open": 100 + i, "high": 100.5 + i, "low": 99.5 + i,
         "close": 100.25 + i, "volume": np.full(n, 1000.0)},
        index=idx)


# --------------------------------------------------------------------------- #
# Interval inference / offering.
# --------------------------------------------------------------------------- #
def test_infers_five_minute_bars():
    assert infer_interval_minutes(_intraday()) == pytest.approx(5.0)


def test_overnight_gap_does_not_skew_inferred_interval():
    """Median, not mean — a 17-hour gap must not read as a 17-hour bar."""
    day1 = _intraday(6, "2026-06-15 15:30")
    day2 = _intraday(6, "2026-06-16 09:30")
    assert infer_interval_minutes(pd.concat([day1, day2])) == pytest.approx(5.0)


def test_daily_frame_offers_no_intraday_intervals():
    daily = _intraday(10, "2026-06-01", freq="1D")
    assert available_intervals(daily) == []


def test_five_minute_frame_offers_exact_multiples_only():
    assert available_intervals(_intraday()) == [5, 15, 30]


def test_thirty_minute_frame_offers_only_itself():
    """No inventing finer candles than the data actually has."""
    assert available_intervals(_intraday(10, freq="30min")) == [30]


def test_native_interval_always_leads_the_list():
    """result[0] is the frame's own bar width — callers rely on that to know
    when a roll-up is needed at all."""
    for freq, base in (("1min", 1), ("5min", 5), ("15min", 15)):
        got = available_intervals(_intraday(10, freq=freq))
        assert got[0] == base, freq
    assert available_intervals(_intraday(10, freq="1min")) == [1, 5, 15, 30]


def test_unknown_interval_is_not_guessed():
    assert available_intervals(None) == []
    assert available_intervals(_intraday(2)) == []


# --------------------------------------------------------------------------- #
# The roll-up itself.
# --------------------------------------------------------------------------- #
def test_three_five_minute_bars_become_one_fifteen_minute_candle():
    out = resample_ohlcv(_intraday(6), 15)
    assert len(out) == 2
    first = out.iloc[0]
    # open from the first bar, close from the last, high/low the extremes.
    assert first["open"] == pytest.approx(100.0)
    assert first["close"] == pytest.approx(102.25)
    assert first["high"] == pytest.approx(102.5)
    assert first["low"] == pytest.approx(99.5)
    assert first["volume"] == pytest.approx(3000.0)


def test_bins_are_left_labelled_and_aligned_to_the_open():
    out = resample_ohlcv(_intraday(6), 15)
    assert list(out.index) == [pd.Timestamp("2026-06-15 09:30"),
                               pd.Timestamp("2026-06-15 09:45")]


def test_overnight_gap_produces_no_empty_candles():
    """resample materializes a bin per gap; all-NaN bins must be dropped."""
    spanning = pd.concat([_intraday(3, "2026-06-15 15:45"),
                          _intraday(3, "2026-06-16 09:30")])
    out = resample_ohlcv(spanning, 15)
    assert len(out) == 2
    assert not out.isna().any().any()


def test_indicator_columns_are_dropped_not_carried_over():
    """RSI on 5m bars is not RSI on 15m bars — stale columns must not survive."""
    df = _intraday(6)
    df["rsi"] = 55.0
    df["sma20"] = 101.0
    assert list(resample_ohlcv(df, 15).columns) == \
        ["open", "high", "low", "close", "volume"]


def test_resampling_to_the_native_interval_is_a_no_op():
    df = _intraday(6)
    out = resample_ohlcv(df, 5)
    pd.testing.assert_frame_equal(out, df[["open", "high", "low", "close", "volume"]],
                                  check_freq=False)


def test_partial_trailing_candle_is_kept():
    """The forming candle matters most in live mode — never discard it."""
    out = resample_ohlcv(_intraday(4), 15)   # 3 bars + 1 orphan
    assert len(out) == 2
    assert out.iloc[-1]["volume"] == pytest.approx(1000.0)


def test_empty_frame_returns_empty_ohlcv():
    assert list(resample_ohlcv(pd.DataFrame(), 15).columns) == \
        ["open", "high", "low", "close", "volume"]


def test_rejects_nonsense_interval():
    with pytest.raises(ValueError):
        resample_ohlcv(_intraday(), 0)
