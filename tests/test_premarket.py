"""Extended-hours (pre-market / after-hours) tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis.premarket import extended_session_signal
from stockanalyzer.analysis.signals import Direction
from stockanalyzer.data.market_session import Session, classify
from stockanalyzer.data.schema import validate_ohlcv

ET = "America/New_York"


# --- session classification ------------------------------------------------
def test_classify_naive_assumed_eastern():
    assert classify(pd.Timestamp("2024-03-05 05:00")) == Session.PRE
    assert classify(pd.Timestamp("2024-03-05 10:00")) == Session.REGULAR
    assert classify(pd.Timestamp("2024-03-05 17:00")) == Session.POST
    assert classify(pd.Timestamp("2024-03-05 22:00")) == Session.CLOSED


def test_classify_tzaware_converted():
    # 13:00 UTC == 08:00 ET (pre-market, winter EST).
    assert classify(pd.Timestamp("2024-01-15 13:00", tz="UTC")) == Session.PRE
    # 18:00 UTC == 13:00 ET (regular).
    assert classify(pd.Timestamp("2024-01-15 18:00", tz="UTC")) == Session.REGULAR


# --- signal ----------------------------------------------------------------
def _intraday(ext_factor: float, session: str = "pre") -> pd.DataFrame:
    reg = pd.date_range("2024-03-04 09:30", "2024-03-04 15:55", freq="5min", tz=ET)
    if session == "pre":
        ext = pd.date_range("2024-03-05 04:00", "2024-03-05 09:00", freq="5min", tz=ET)
    elif session == "post":
        ext = pd.date_range("2024-03-04 16:05", "2024-03-04 19:55", freq="5min", tz=ET)
    else:
        ext = pd.DatetimeIndex([])
    idx = reg.append(ext)
    close = np.concatenate([np.full(len(reg), 100.0), np.full(len(ext), 100.0 * ext_factor)])
    vol = np.concatenate([np.full(len(reg), 1e6), np.zeros(len(ext))])
    return validate_ohlcv(pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close, "volume": vol},
        index=idx))


def test_premarket_gap_up_is_bullish():
    sig = extended_session_signal(_intraday(1.02, "pre"))
    assert sig is not None
    assert sig.name == "premarket_gap_up"
    assert sig.direction == Direction.BULL
    assert sig.meta["gap_pct"] == 2.0


def test_premarket_gap_down_is_bearish():
    sig = extended_session_signal(_intraday(0.97, "pre"))
    assert sig is not None and sig.name == "premarket_gap_down"
    assert sig.direction == Direction.BEAR


def test_afterhours_move_detected():
    sig = extended_session_signal(_intraday(1.03, "post"))
    assert sig is not None and sig.name == "afterhours_move_up"


def test_no_signal_when_only_regular():
    # Latest bar is a regular-session bar → no extended signal.
    reg = pd.date_range("2024-03-04 09:30", "2024-03-04 15:55", freq="5min", tz=ET)
    close = np.full(len(reg), 100.0)
    df = validate_ohlcv(pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
         "volume": np.full(len(reg), 1e6)}, index=reg))
    assert extended_session_signal(df) is None


def test_no_signal_on_daily_frame():
    idx = pd.date_range("2024-01-01", periods=30, freq="D")
    close = np.linspace(100, 110, 30)
    df = validate_ohlcv(pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close,
         "volume": np.full(30, 1e6)}, index=idx))
    assert extended_session_signal(df) is None


def test_small_gap_ignored():
    assert extended_session_signal(_intraday(1.001, "pre")) is None  # 0.1% < 0.4%
