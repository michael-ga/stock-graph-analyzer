"""Golden-data tests: build synthetic OHLCV with a known shape and assert the
right detector fires with the right direction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.analysis.signals import Direction
from stockanalyzer.analysis.trend import detect_trend, find_swings
from stockanalyzer.data.schema import validate_ohlcv


def _frame(closes: list[float], vol: float = 1_000_000.0) -> pd.DataFrame:
    """Build a clean OHLCV frame from a close path (small symmetric wicks)."""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    closes = np.array(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.5
    lows = np.minimum(opens, closes) - 0.5
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(len(closes), vol)},
        index=idx,
    )
    return validate_ohlcv(df)


def test_validate_ohlcv_rejects_empty():
    with pytest.raises(ValueError):
        validate_ohlcv(pd.DataFrame())


def test_uptrend_detected():
    # Rising zig-zag → higher highs and higher lows.
    path = []
    base = 100.0
    for i in range(8):
        base += 5
        path += [base, base - 2, base + 1, base - 1]
    df = _frame(path)
    sig = detect_trend(df)
    assert sig.direction == Direction.BULL


def test_downtrend_detected():
    path = []
    base = 200.0
    for i in range(8):
        base -= 5
        path += [base, base + 2, base - 1, base + 1]
    df = _frame(path)
    sig = detect_trend(df)
    assert sig.direction == Direction.BEAR


def test_find_swings_nonempty():
    df = _frame([10, 12, 11, 14, 9, 15, 8, 16, 7, 17])
    swings = find_swings(df, order=1)
    assert any(s.kind == "high" for s in swings)
    assert any(s.kind == "low" for s in swings)


def test_double_top_is_bearish():
    # Up to a peak, down to a trough, back to a similar peak, then break below trough.
    path = (
        list(np.linspace(100, 130, 12))
        + list(np.linspace(130, 110, 8))      # trough ~110
        + list(np.linspace(110, 129.5, 8))    # second peak ~ first
        + list(np.linspace(129.5, 104, 10))   # break below neckline
    )
    df = _frame(path)
    rep = analyze_timeframe(df, order=2)
    names = [s.name for s in rep.signals]
    assert "double_top" in names
    dt = next(s for s in rep.signals if s.name == "double_top")
    assert dt.direction == Direction.BEAR


def test_engine_runs_and_scores():
    df = _frame(list(np.linspace(50, 80, 60)))
    rep = analyze_timeframe(df)
    assert -1.0 <= rep.bias_score <= 1.0
    assert rep.trend.direction == Direction.BULL  # steady rise
    assert "last_close" in rep.meta


def test_rsi_bounds():
    from stockanalyzer.analysis.indicators import rsi

    df = _frame(list(np.linspace(10, 50, 40)))
    r = rsi(df["close"]).dropna()
    assert (r >= 0).all() and (r <= 100).all()
