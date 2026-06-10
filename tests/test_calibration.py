"""Calibration suite — the three real-world failure cases, frozen as regressions.

INTC (room failure): walls just overhead + post-news volatility → must NOT be an
immediate GO; honest answer is wait-for-breakout (or no trade), never a target
inflated through ceilings.

MSFT (direction failure): countertrend intraday reversal against a bearish frame
in a chop zone → no_trade, conflict flagged, no fabricated +6% aim.

NOK (coherence failure): no setup, tiny first-wall room, 8%/day volatility → the
plan must stay honest (no fake aim; vol-sized protective stop), kind=breakout_wait
with the real trigger or no_trade.

Clean pullback: the fixes must NOT kill real GOs — uptrend pullback with room and
calm volatility stays GO with score ≥ 70.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis.engine import TimeframeReport
from stockanalyzer.analysis.levels import Level
from stockanalyzer.analysis.signals import Direction, Signal
from stockanalyzer.analysis.trend_change import TrendChange
from stockanalyzer.data.schema import Timeframe, validate_ohlcv
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.strategy import SwingPace


# --------------------------------------------------------------------------- #
# Fixture builders: fully controlled frames + reports.
# --------------------------------------------------------------------------- #
def _frame(price: float, daily_range_pct: float, n: int = 60,
           drift_pct: float = 0.0, last_jump_pct: float = 0.0) -> pd.DataFrame:
    """Daily OHLCV frame ending at `price` with controlled daily range (≈ATR)."""
    idx = pd.date_range("2026-01-02", periods=n, freq="B")
    drift = price * drift_pct / 100.0
    closes = np.linspace(price - drift, price, n)
    if last_jump_pct:
        closes[-2] = closes[-1] / (1 + last_jump_pct / 100.0)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    half = closes * daily_range_pct / 200.0
    highs = np.maximum(opens, closes) + half
    lows = np.minimum(opens, closes) - half
    return validate_ohlcv(pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(n, 1e6)}, index=idx))


def _ind(df: pd.DataFrame, *, sma20=None, sma50=None, sma200=None, ema20=None,
         rsi=50.0, macd=0.0, macd_signal=0.0, atr_pct=2.0) -> pd.DataFrame:
    """Attach controlled indicator columns (constant series = last value rules)."""
    out = df.copy()
    n = len(df)
    price = float(df["close"].iloc[-1])
    out["sma20"] = np.full(n, sma20 if sma20 is not None else price)
    out["sma50"] = np.full(n, sma50 if sma50 is not None else price * 0.97)
    out["sma200"] = np.full(n, sma200 if sma200 is not None else price * 0.90)
    out["ema20"] = np.linspace((ema20 or price) * 0.98, ema20 if ema20 is not None else price, n)
    out["rsi"] = np.full(n, rsi)
    out["macd"] = np.full(n, macd)
    out["macd_signal"] = np.full(n, macd_signal)
    out["atr"] = np.full(n, price * atr_pct / 100.0)
    return out


def _report(df: pd.DataFrame, *, levels=(), bias=0.0, trend_dir=Direction.NEUTRAL,
            tc: TrendChange | None = None, signals=()) -> TimeframeReport:
    price = float(df["close"].iloc[-1])
    trend = Signal("trend", trend_dir, 0.6, "fixture trend", "trend")
    return TimeframeReport(
        df=df, trend=trend, signals=[trend, *signals],
        levels=list(levels), trendlines={},
        trend_change=tc or TrendChange(False, Direction.NEUTRAL, 0.0, []),
        bias_score=bias, meta={"last_close": price, "bars": len(df)},
    )


def _lv(price: float, kind: str) -> Level:
    return Level(price=price, touches=3, kind=kind, strength=0.7)


# --------------------------------------------------------------------------- #
# INTC replica — room failure.
# --------------------------------------------------------------------------- #
def _intc_reports():
    p = 108.36
    dec = _report(
        _ind(_frame(p, 2.0), atr_pct=1.1, rsi=63, macd=1.0, macd_signal=0.5,
             sma50=p * 0.95, sma200=p * 0.80),
        levels=[_lv(109.74, "resistance"), _lv(112.98, "resistance"),
                _lv(107.98, "support"), _lv(103.57, "support")],
        bias=0.31, trend_dir=Direction.BULL,
        signals=[Signal("volume_spike", Direction.BULL, 0.6, "volume 2x average", "volume")],
    )
    m6 = _report(_ind(_frame(p, 8.6, last_jump_pct=8.5), atr_pct=8.6, rsi=59,
                      macd=2.0, macd_signal=1.0, sma20=p * 0.93),
                 bias=-0.46)
    return dec, {Timeframe.D5: dec, Timeframe.M6: m6, Timeframe.M1: m6,
                 Timeframe.Y1: m6, Timeframe.D1: _report(_ind(_frame(p, 1.0)), bias=-0.46)}


def test_intc_replica_no_immediate_go():
    dec, reports = _intc_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 55})
    assert plan.kind in ("breakout_wait", "no_trade")
    assert plan.go is False
    assert plan.score < 60, f"score {plan.score} should be <60 for the INTC trap"


def test_intc_replica_target_never_through_walls():
    dec, reports = _intc_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports)
    if plan.kind == "breakout_wait":
        # Target must sit at/below the wall after the trigger, never beyond it.
        assert plan.trigger in (109.74, 112.98)
        assert plan.target1 <= 112.98 * 1.001 or plan.trigger == 112.98
        assert "close above" in plan.guidance.lower()
    else:
        assert plan.kind == "no_trade"


def test_intc_replica_chase_flagged():
    dec, reports = _intc_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports)
    chase = next((c for c in plan.checks if "chase" in c.name.lower()), None)
    assert chase is not None and chase.ok is False     # the +8.5% day = chase risk


# --------------------------------------------------------------------------- #
# MSFT replica — direction failure (countertrend in chop).
# --------------------------------------------------------------------------- #
def _msft_reports():
    p = 403.43
    dec = _report(
        _ind(_frame(p, 1.0), atr_pct=0.9, rsi=55, macd=-0.5, macd_signal=0.0,
             sma50=p * 0.99, sma200=p * 0.92),
        levels=[_lv(404.47, "resistance"), _lv(410.57, "resistance"),
                _lv(415.92, "resistance"), _lv(428.03, "resistance"),
                _lv(397.43, "support")],
        bias=-0.54, trend_dir=Direction.BEAR,
        tc=TrendChange(True, Direction.BULL, 0.64,
                       ["RSI bullish divergence (30-min bars)", "Hammer (30-min bars)"]),
    )
    m6 = _report(_ind(_frame(p, 2.9), atr_pct=2.9, rsi=45, macd=-1.0, macd_signal=0.5,
                      sma20=p * 1.02, sma50=p * 1.03), bias=-0.57)
    m1 = _report(_ind(_frame(p, 2.9), atr_pct=2.9, rsi=34), bias=-0.42)
    return dec, {Timeframe.D5: dec, Timeframe.M6: m6, Timeframe.M1: m1,
                 Timeframe.Y1: m6, Timeframe.D1: _report(_ind(_frame(p, 0.5)), bias=0.0)}


def test_msft_replica_countertrend_is_not_go():
    dec, reports = _msft_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 44})
    assert plan.setup == "Trend-change reversal (early)"
    assert plan.go is False
    assert plan.kind == "no_trade"            # bounce room +0.26% is far below minimum
    assert plan.score < 55, f"score {plan.score}"


def test_msft_replica_no_fabricated_target():
    dec, reports = _msft_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 44})
    # The old code bumped the target to +6% through three walls. Honest target
    # stays at/below the first wall for a countertrend bounce.
    assert plan.target1 <= 404.47 * 1.001
    assert abs(plan.target1_pct) < 3.0


def test_msft_replica_conflict_flagged():
    dec, reports = _msft_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 44})
    row = next((c for c in plan.checks if "conflict" in c.name.lower()), None)
    assert row is not None and row.ok is False
    assert any("countertrend" in r.lower() for r in plan.reasons)


# --------------------------------------------------------------------------- #
# NOK replica — coherence failure (no setup must not fake numbers).
# --------------------------------------------------------------------------- #
def _nok_reports():
    p = 13.80
    dec = _report(
        _ind(_frame(p, 1.5), atr_pct=1.5, rsi=52, macd=0.0, macd_signal=0.1,
             sma50=p * 1.02, sma200=p * 0.85),
        levels=[_lv(13.94, "resistance"), _lv(14.57, "resistance"),
                _lv(13.61, "support"), _lv(13.29, "support")],
        bias=-1.0, trend_dir=Direction.BEAR,
    )
    # 6M context is explicitly NOT an uptrend (price under its 50-MA, EMA above
    # price) so the fixed indicator-frame setup detection still finds no setup.
    m6 = _report(_ind(_frame(p, 8.3), atr_pct=8.3, rsi=47, sma20=p * 0.99,
                      sma50=p * 1.05, ema20=p * 1.06), bias=0.15)
    return dec, {Timeframe.D5: dec, Timeframe.M6: m6, Timeframe.M1: m6,
                 Timeframe.Y1: m6, Timeframe.D1: _report(_ind(_frame(p, 0.8)), bias=0.0)}


def test_nok_replica_no_setup_no_fake_aim():
    dec, reports = _nok_reports()
    plan = build_swing_plan(dec, UseCase.OWN, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 59})
    assert plan.setup == "No setup"
    assert plan.go is False
    # Honest geometry: target at a real wall, never a +6% fabrication.
    assert plan.target1 <= 14.57 * 1.001
    # Protective stop is vol-sized (~0.7 × 8.3% ≈ 5.8%), not a noise-level −1.8%.
    assert abs(plan.stop_pct) >= 4.0, f"stop {plan.stop_pct}% is inside daily noise"


def test_nok_replica_breakout_trigger_is_real_wall():
    dec, reports = _nok_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports)
    if plan.kind == "breakout_wait":
        assert plan.trigger == 13.94
        assert plan.target1 <= 14.57 * 1.001
    else:
        assert plan.kind == "no_trade"


# --------------------------------------------------------------------------- #
# Clean pullback — the fixes must NOT kill real GOs.
# --------------------------------------------------------------------------- #
def _clean_pullback_reports():
    p = 100.0
    dec = _report(
        _ind(_frame(p, 2.0, drift_pct=10), atr_pct=2.0, rsi=48, macd=0.6,
             macd_signal=0.3, sma20=p * 0.995, sma50=p * 0.96, sma200=p * 0.88,
             ema20=p * 0.995),
        levels=[_lv(107.0, "resistance"), _lv(98.5, "support")],
        bias=0.5, trend_dir=Direction.BULL,
    )
    bull6 = _report(_ind(_frame(p, 2.0, drift_pct=15), atr_pct=2.0, rsi=55,
                         macd=1.0, macd_signal=0.4, sma20=p * 0.97, sma50=p * 0.94,
                         sma200=p * 0.85), bias=0.4)
    d1 = _report(_ind(_frame(p, 1.0)), bias=0.3)
    m1 = _report(_ind(_frame(p, 2.0), rsi=55), bias=0.35)
    return dec, {Timeframe.D5: dec, Timeframe.M6: bull6, Timeframe.M1: m1,
                 Timeframe.Y1: bull6, Timeframe.D1: d1}


def test_clean_pullback_stays_go_with_strong_score():
    dec, reports = _clean_pullback_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 62})
    assert plan.setup in ("Pullback to 20-EMA", "Support test (uptrend)")
    assert plan.kind == "immediate"
    assert plan.go is True, f"clean pullback must stay GO (guidance: {plan.guidance})"
    assert plan.rr >= 2.0
    assert plan.score >= 70, f"score {plan.score} should be ≥70 on a clean setup"
    assert "enter" in plan.guidance.lower()


def test_clean_pullback_target_honest():
    dec, reports = _clean_pullback_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports)
    # Target is capped by the vol budget (2% × √3 ≈ 3.5%), under the 107 wall.
    assert plan.target1 < 107.0
    assert 3.0 <= plan.target1_pct <= 4.0


# --------------------------------------------------------------------------- #
# Cross-cutting honesty properties.
# --------------------------------------------------------------------------- #
def test_earnings_proximity_blocks_go():
    dec, reports = _clean_pullback_reports()
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 62, "earnings_days": 2})
    assert plan.go is False
    assert "earnings" in plan.guidance.lower()


def test_na_rows_do_not_inflate_score():
    dec, reports = _clean_pullback_reports()
    full = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=reports,
                            context={"investor_pct": 62})
    # Strip the 6M frame's indicator history → MA/MACD rows become n/a.
    gutted_df = reports[Timeframe.M6].df.copy()
    for col in ("sma50", "macd", "macd_signal"):
        gutted_df[col] = float("nan")
    gutted = dict(reports)
    gutted[Timeframe.M6] = _report(gutted_df, bias=0.4)
    partial = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, all_reports=gutted,
                               context={"investor_pct": 62})
    na_rows = [c for c in partial.checks if c.na]
    assert na_rows, "missing history must surface as visible n/a rows"
    assert partial.score <= full.score, "n/a rows must never raise the score"


def test_countertrend_scores_below_withtrend_same_geometry():
    dec, reports = _clean_pullback_reports()
    with_trend = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST,
                                  all_reports=reports, context={"investor_pct": 62})
    ct_dec = _report(dec.df, levels=dec.levels, bias=dec.bias_score,
                     trend_dir=Direction.BULL,
                     tc=TrendChange(True, Direction.BULL, 0.6, ["fixture divergence"]))
    counter = build_swing_plan(ct_dec, UseCase.BUY, SwingPace.FAST,
                               all_reports=reports, context={"investor_pct": 62})
    assert counter.setup == "Trend-change reversal (early)"
    assert counter.score <= with_trend.score
