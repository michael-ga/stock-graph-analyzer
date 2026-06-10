"""Tests for the no-AI explanation layer + new timeframes."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.data.schema import TIMEFRAME_SPECS, Timeframe, validate_ohlcv
from stockanalyzer.explain.glossary import TERMS, explain_signal
from stockanalyzer.explain.recommend import build_recommendation, preset_for
from stockanalyzer.explain.scenarios import build_scenario
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.verdict.aggregate import TIMEFRAME_WEIGHTS, build_verdict

# All signal names the engine can emit (keep in sync with analysis/*).
EMITTED = {
    "trend", "near_support", "near_resistance", "uptrend_line_break",
    "downtrend_line_break", "golden_cross", "death_cross", "rsi_overbought",
    "rsi_oversold", "rsi_bearish_divergence", "rsi_bullish_divergence",
    "macd_bull_cross", "macd_bear_cross", "stoch_bull", "stoch_bear",
    "volume_spike", "doji", "hammer", "shooting_star", "bullish_engulfing",
    "bearish_engulfing", "harami", "double_top", "double_bottom",
    "head_and_shoulders", "inverse_head_and_shoulders",
    "premarket_gap_up", "premarket_gap_down",
    "afterhours_move_up", "afterhours_move_down",
}


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=len(closes), freq="D")
    closes = np.array(closes, float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.5
    lows = np.minimum(opens, closes) - 0.5
    return validate_ohlcv(pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(len(closes), 1e6)}, index=idx))


def _reports(uptrend: bool):
    # Wavy trend (drift + oscillation) so it forms real higher/lower swings and
    # RSI isn't pinned at an extreme — closer to a real chart than a straight line.
    t = np.arange(120)
    drift = 0.3 * t if uptrend else -0.3 * t
    closes = list(80 + drift + 5 * np.sin(t / 6.0))
    rep = analyze_timeframe(_frame(closes))
    return {Timeframe.Y1: rep, Timeframe.M6: rep}


# --- glossary --------------------------------------------------------------
def test_glossary_covers_all_emitted_signals():
    missing = EMITTED - set(TERMS)
    assert not missing, f"glossary missing terms: {missing}"


def test_glossary_fallback():
    t = explain_signal("totally_unknown_signal")
    assert t.title and t.layman


# --- presets ---------------------------------------------------------------
def test_preset_buckets():
    assert preset_for(0.6).key == "strong_buy"
    assert preset_for(0.5).key == "strong_buy"
    assert preset_for(0.3).key == "lean_buy"
    assert preset_for(0.2).key == "lean_buy"
    assert preset_for(0.0).key == "neutral"
    assert preset_for(-0.3).key == "lean_sell"
    assert preset_for(-0.6).key == "strong_sell"


# --- recommendation --------------------------------------------------------
def test_recommendation_buy_vs_sell_go_score_flips():
    reports = _reports(uptrend=True)
    verdict = build_verdict(reports)
    buy = build_recommendation("TEST", verdict, reports, UseCase.BUY)
    sell = build_recommendation("TEST", verdict, reports, UseCase.SELL)
    assert buy.bullish_pct == sell.bullish_pct
    assert buy.go_score + sell.go_score == 100
    assert "TEST" in buy.summary


def test_uptrend_reads_bullish_for_buyer():
    reports = _reports(uptrend=True)
    verdict = build_verdict(reports)
    buy = build_recommendation("UP", verdict, reports, UseCase.BUY)
    assert buy.bullish_pct >= 50
    assert buy.scenario is not None


# --- scenarios -------------------------------------------------------------
def test_scenario_levels_split_around_price():
    rep = analyze_timeframe(_frame(
        list(np.linspace(100, 130, 20)) + list(np.linspace(130, 110, 15))
        + list(np.linspace(110, 125, 15))))
    sc = build_scenario(rep)
    price = rep.meta["last_close"]
    assert all(l > price for l in sc.upside_levels)
    assert all(l < price for l in sc.downside_levels)
    assert sc.ordered


# --- schema / weights ------------------------------------------------------
def test_new_timeframes_present_and_ordered():
    order = [tf.value for tf in Timeframe]
    assert order == ["1D", "5D", "1M", "6M", "YTD", "1Y", "5Y"]
    for tf in Timeframe:
        assert tf in TIMEFRAME_SPECS
        assert tf in TIMEFRAME_WEIGHTS
