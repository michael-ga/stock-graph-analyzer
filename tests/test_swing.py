"""Swing-mode tests: trade plan, R:R gate, strategy weighting, decision timeframe."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.analysis.signals import Direction
from stockanalyzer.data.schema import Timeframe, validate_ohlcv
from stockanalyzer.explain.recommend import build_recommendation
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.strategy import Strategy, SwingPace
from stockanalyzer.verdict.aggregate import build_verdict


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=len(closes), freq="D")
    closes = np.array(closes, float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.5
    lows = np.minimum(opens, closes) - 0.5
    return validate_ohlcv(pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(len(closes), 1e6)}, index=idx))


def _uptrend(n=120):
    t = np.arange(n)
    return analyze_timeframe(_frame(list(80 + 0.3 * t + 5 * np.sin(t / 6.0))))


def _downtrend(n=120):
    t = np.arange(n)
    return analyze_timeframe(_frame(list(120 - 0.3 * t + 5 * np.sin(t / 6.0))))


# --- trade plan structure --------------------------------------------------
def test_long_plan_enters_near_price_not_far_resistance():
    plan = build_swing_plan(_uptrend(), UseCase.BUY)
    assert plan is not None
    assert plan.bias == Direction.BULL
    price = 80 + 0.3 * 119  # approx last close region; just assert entry ~ last close
    assert plan.stop < plan.entry < plan.target1          # ordered correctly
    assert plan.target1_pct > 0                            # target is above entry
    assert abs(plan.entry - price) < price * 0.2           # entry is near current price
    # The whole point: target is a *swing* target, not a far-off breakout.
    assert plan.target1_pct <= 16.0


def test_go_implies_rr_at_least_two():
    for rep in (_uptrend(), _downtrend()):
        for uc in (UseCase.BUY, UseCase.SELL):
            plan = build_swing_plan(rep, uc)
            if plan.go:
                assert plan.rr >= 2.0


def test_downtrend_has_no_long_setup():
    plan = build_swing_plan(_downtrend(), UseCase.BUY)
    assert plan.go is False
    assert plan.light in ("no", "forming")


def test_sell_plan_is_a_short():
    plan = build_swing_plan(_downtrend(), UseCase.SELL)
    assert plan.bias == Direction.BEAR
    # Short: stop above entry, target below.
    assert plan.target1 < plan.entry < plan.stop


# --- strategy weighting ----------------------------------------------------
def test_swing_weights_favor_short_timeframes():
    reports = {Timeframe.D1: _uptrend(), Timeframe.Y1: _downtrend()}
    investor = build_verdict(reports, strategy=Strategy.INVESTOR).score
    swing = build_verdict(reports, strategy=Strategy.SWING).score
    # Short-term bullish + long-term bearish → swing reads more bullish than investor.
    assert swing > investor


# --- decision timeframe ----------------------------------------------------
def test_decision_timeframe_differs_by_strategy():
    reports = {Timeframe.M1: _uptrend(), Timeframe.Y1: _uptrend()}
    v = build_verdict(reports)
    inv = build_recommendation("X", v, reports, UseCase.BUY, Strategy.INVESTOR)
    sw = build_recommendation("X", v, reports, UseCase.BUY, Strategy.SWING)
    assert inv.decision_timeframe == "1Y"
    assert sw.decision_timeframe == "1M"
    assert sw.swing is not None and inv.swing is None


# --- swing score -------------------------------------------------------------
def test_plan_has_score_and_checks():
    plan = build_swing_plan(_uptrend(), UseCase.BUY)
    assert 0 <= plan.score <= 100
    assert plan.score_label in ("Strong", "Good", "Weak — wait", "Avoid")
    assert plan.checks, "score must come with a plain-English checklist"
    names = " ".join(c.name for c in plan.checks)
    assert "setup" in names.lower() and "2× risk" in names


def test_score_uses_5d_1m_1y_ma_and_macd():
    up = _uptrend()
    reports = {Timeframe.D5: up, Timeframe.M1: up, Timeframe.Y1: up}
    plan = build_swing_plan(up, UseCase.BUY, all_reports=reports)
    names = " ".join(c.name for c in plan.checks)
    for tf in ("5D", "1M", "1Y"):
        assert tf in names
    assert "MACD" in names


def test_score_higher_when_timeframes_align():
    up, down = _uptrend(), _downtrend()
    aligned = build_swing_plan(up, UseCase.BUY,
                               all_reports={Timeframe.D5: up, Timeframe.M1: up,
                                            Timeframe.Y1: up})
    conflicted = build_swing_plan(up, UseCase.BUY,
                                  all_reports={Timeframe.D5: up, Timeframe.M1: down,
                                               Timeframe.Y1: down})
    assert aligned.score > conflicted.score


def test_go_requires_min_move_of_3pct():
    for rep in (_uptrend(), _downtrend()):
        for uc in (UseCase.BUY, UseCase.SELL):
            plan = build_swing_plan(rep, uc)
            if plan.go:
                assert abs(plan.target1_pct) >= 3.0


def test_price_override_recomputes_entry_live():
    rep = _uptrend()
    base = build_swing_plan(rep, UseCase.BUY)
    live = base.entry * 1.05                       # simulate a live tick 5% higher
    overridden = build_swing_plan(rep, UseCase.BUY, price_override=live)
    assert abs(overridden.entry - live) < 0.01     # entry tracks the live price
    assert overridden.entry > base.entry           # and moved up with it
    assert overridden.stop < overridden.entry < overridden.target1  # still ordered


def test_recommendation_price_override_threads_to_plan():
    reports = {Timeframe.M1: _uptrend()}
    v = build_verdict(reports, strategy=Strategy.SWING)
    base = build_recommendation("X", v, reports, UseCase.BUY, Strategy.SWING)
    live = base.swing.entry * 1.03
    rec = build_recommendation("X", v, reports, UseCase.BUY, Strategy.SWING,
                               price_override=live)
    assert abs(rec.swing.entry - live) < 0.01


def test_fast_pace_uses_short_chart_and_tighter_stop():
    reports = {Timeframe.D5: _uptrend(), Timeframe.M1: _uptrend()}
    v = build_verdict(reports, strategy=Strategy.SWING, pace=SwingPace.FAST)
    fast = build_recommendation("X", v, reports, UseCase.BUY, Strategy.SWING, SwingPace.FAST)
    std = build_recommendation("X", v, reports, UseCase.BUY, Strategy.SWING, SwingPace.STANDARD)
    assert fast.decision_timeframe == "5D"     # fast decides on the 5D chart
    assert std.decision_timeframe == "1M"      # standard on the 1-month chart
    assert fast.swing.horizon == "1–3 days"

    rep = _uptrend()
    f = build_swing_plan(rep, UseCase.BUY, SwingPace.FAST)
    s = build_swing_plan(rep, UseCase.BUY, SwingPace.STANDARD)
    assert f.risk_pct <= s.risk_pct + 0.01     # fast uses a tighter ATR stop
    assert f.target1_pct <= s.target1_pct + 0.01  # and a slightly tighter target cap
