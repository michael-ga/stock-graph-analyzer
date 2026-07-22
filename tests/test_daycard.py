"""Day-trader card: build a synthetic intraday session with a known shape and
assert each focused number is computed correctly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis.daycard import build_day_card
from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.data.schema import Timeframe, validate_ohlcv


def _intraday_frame(closes: list[float], vols: list[float] | None = None) -> pd.DataFrame:
    """5-minute bars inside a single regular session (so it reads as intraday)."""
    idx = pd.date_range("2024-03-01 09:30", periods=len(closes), freq="5min")
    closes = np.array(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.25
    lows = np.minimum(opens, closes) - 0.25
    vol = np.array(vols, dtype=float) if vols is not None else np.full(len(closes), 1_000_000.0)
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vol},
        index=idx,
    )
    return validate_ohlcv(df)


def _reports(df: pd.DataFrame) -> dict:
    return {Timeframe.D1: analyze_timeframe(df)}


def test_empty_reports_returns_none():
    assert build_day_card({}, 100.0) is None


def test_session_range_and_bias_split():
    # Rises 100→120 then eases to 112 — a clean up-day with a pullback.
    path = list(np.linspace(100, 120, 30)) + list(np.linspace(120, 112, 10))
    df = _intraday_frame(path)
    card = build_day_card(_reports(df), current_price=float(df["close"].iloc[-1]))

    assert card is not None
    assert card.intraday and card.n_session_bars == len(path)
    # Session extremes bracket the current price.
    assert card.session_low <= card.current_price <= card.session_high
    assert card.session_high >= 120.0          # the peak (plus wick) is captured
    # Bull/bear split is a complete, volume-weighted partition.
    assert abs(card.bull_pct + card.bear_pct - 100.0) < 0.1
    assert card.bull_pct > card.bear_pct       # mostly-up session
    assert card.bias_label == "Bullish"
    assert card.day_change_pct is not None and card.day_change_pct > 0
    # Range position is a 0..100 reading of where price sits between low and high.
    assert card.range_position_pct is not None
    assert 0.0 <= card.range_position_pct <= 100.0
    # Closed at ~112 in a 100→121 range → comfortably in the upper-middle.
    assert card.range_position_pct > 50.0


def test_levels_split_by_side_of_price():
    path = list(np.linspace(100, 120, 30)) + list(np.linspace(120, 112, 10))
    df = _intraday_frame(path)
    price = float(df["close"].iloc[-1])
    card = build_day_card(_reports(df), current_price=price)

    assert all(r > card.current_price for r in card.resistances)
    assert all(s < card.current_price for s in card.supports)
    # Next target / support are the nearest level on each side.
    if card.resistances:
        assert card.next_target == card.resistances[0]
        assert card.next_target_pct is not None and card.next_target_pct > 0
    if card.supports:
        assert card.next_support == card.supports[0]
        assert card.next_support_pct is not None and card.next_support_pct < 0


def test_battle_zones_are_priced_within_session_range():
    # Concentrate volume in the middle of the range → the heaviest node sits there.
    path = list(np.linspace(100, 110, 20)) + list(np.linspace(110, 100, 20))
    vols = [200_000.0] * 18 + [5_000_000.0] * 4 + [200_000.0] * 18
    df = _intraday_frame(path, vols)
    card = build_day_card(_reports(df), current_price=float(df["close"].iloc[-1]))

    assert card.battle_zones
    for z in card.battle_zones:
        assert card.session_low <= z.price <= card.session_high
        assert 0 < z.volume_pct <= 100
    # The heaviest-volume node should sit near the high (~110) where the big bars traded.
    assert card.battle_zones[0].price > 106.0


def test_current_price_derived_when_not_passed():
    path = list(np.linspace(50, 60, 40))
    df = _intraday_frame(path)
    card = build_day_card(_reports(df))          # no explicit price
    assert card is not None
    assert abs(card.current_price - float(df["close"].iloc[-1])) < 0.5
