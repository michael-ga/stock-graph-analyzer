"""Emerging-mode demand score: rewards genuine eager buying, refuses to reward a
blowoff top, and never lets thin history look high-conviction."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.data.schema import Timeframe, validate_ohlcv
from stockanalyzer.explain.emerging import (EMERGING_SCORE_CAP, EmergingScore,
                                            score_emerging)
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.explain.usecase import UseCase


def _bars(rows: list[tuple[float, float, float, float]],
          vol: list[float] | None = None) -> pd.DataFrame:
    """rows = list of (open, high, low, close)."""
    o, h, l, c = (np.array(x, float) for x in zip(*rows))
    v = np.array(vol, float) if vol is not None else np.full(len(rows), 1e6)
    idx = pd.date_range("2026-01-02", periods=len(rows), freq="B")
    return validate_ohlcv(pd.DataFrame(
        {"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx))


# --------------------------------------------------------------------------- #
# Core behavior: genuine demand vs distribution.
# --------------------------------------------------------------------------- #
def test_eager_buying_scores_high():
    # Higher lows, strong closes near the high, rising volume — genuine demand,
    # but only modestly extended above the anchor.
    df = _bars([(100, 104, 99, 103), (103, 108, 102, 107),
                (107, 112, 106, 111)],
               vol=[1e6, 1.4e6, 1.8e6])
    es = score_emerging(df, offering_price=100.0)
    assert es.score >= 60
    assert es.components["accumulation"] >= 0.8
    assert es.components["close"] >= 0.7
    assert "Healthy demand" in es.label or "Building" in es.label
    assert not es.extended


def test_big_green_but_weak_close_is_not_demand():
    # The SPCX lesson: huge percentage move, but each bar closes near its low.
    # Same headline % as a strong day, far lower demand score.
    strong = _bars([(100, 120, 99, 119)])          # closed on the high
    weak = _bars([(100, 120, 99, 102)])            # spiked then sold off
    s_strong = score_emerging(strong, offering_price=100.0)
    s_weak = score_emerging(weak, offering_price=100.0)
    assert s_weak.score < s_strong.score
    assert s_weak.components["close"] < 0.3


def test_parabolic_move_flags_extended_and_zeroes_guard():
    # Vertical: +35% above the anchor → extension guard collapses, extended fires.
    df = _bars([(100, 112, 99, 110), (110, 126, 109, 124), (124, 140, 123, 138)],
               vol=[1e6, 1.2e6, 1.5e6])
    es = score_emerging(df, offering_price=100.0)
    assert es.extended
    assert "extended" in es.flags
    assert es.components["extension"] <= 0.2
    assert "extended" in es.label.lower()


def test_distribution_pattern_flagged():
    # Up big off the anchor, but every bar spikes then closes weak on huge upper
    # wicks = the move is being handed to latecomers.
    df = _bars([(100, 128, 99, 108), (108, 132, 105, 110), (110, 138, 107, 111)])
    es = score_emerging(df, offering_price=100.0)
    assert "distribution_risk" in es.flags
    assert "Distribution" in es.label


# --------------------------------------------------------------------------- #
# Guardrails.
# --------------------------------------------------------------------------- #
def test_score_is_capped():
    # Even a flawless-looking single strong bar can't exceed the cap.
    df = _bars([(100, 105, 100, 105), (105, 110, 105, 110), (110, 111, 110, 111)],
               vol=[1e6, 2e6, 3e6])
    es = score_emerging(df, offering_price=100.0, vwap=108.0)
    assert es.score <= EMERGING_SCORE_CAP


def test_confidence_scales_with_history():
    three = _bars([(100, 104, 99, 103)] * 3)
    thirty = _bars([(100, 104, 99, 103)] * 30)
    s3 = score_emerging(three, offering_price=100.0)
    s30 = score_emerging(thirty, offering_price=100.0)
    assert s3.confidence < s30.confidence
    assert s3.confidence <= 0.1                     # ~3 bars ≈ 0.05
    # A thin-history name is never called "healthy demand", regardless of score.
    assert "Healthy demand" not in s3.label


def test_vwap_term_dropped_when_absent():
    df = _bars([(100, 104, 99, 103), (103, 108, 102, 107)])
    with_vwap = score_emerging(df, offering_price=100.0, vwap=106.0)
    without = score_emerging(df, offering_price=100.0)
    assert "vwap" in with_vwap.components
    assert "vwap" not in without.components
    # Both still produce a valid, bounded score.
    for es in (with_vwap, without):
        assert 0 <= es.score <= EMERGING_SCORE_CAP


def test_anchor_falls_back_to_first_open():
    df = _bars([(50, 55, 49, 54), (54, 60, 53, 59)])
    es = score_emerging(df)                          # no offering price supplied
    assert es.anchor == 50.0


# --------------------------------------------------------------------------- #
# Swing-plan integration: routing + GO veto.
# --------------------------------------------------------------------------- #
def _ipo_report(closes: list[float]):
    closes = np.array(closes, float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * 1.01
    lows = np.minimum(opens, closes) * 0.99
    idx = pd.date_range("2026-05-01", periods=len(closes), freq="B")
    df = validate_ohlcv(pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(len(closes), 1e6)}, index=idx))
    return analyze_timeframe(df)


def test_swing_plan_routes_to_emerging_for_short_history():
    rep = _ipo_report([100, 103, 106, 109, 112])     # 5 bars only
    plan = build_swing_plan(rep, UseCase.BUY, all_reports={Timeframe.M1: rep})
    assert plan.emerging is not None
    assert plan.score == plan.emerging.score
    assert plan.score_label == plan.emerging.label
    assert plan.confidence == "low"


def test_swing_plan_keeps_mature_score_with_full_history():
    closes = list(80 + 0.3 * np.arange(120))
    rep = _ipo_report(closes)                         # 120 bars
    plan = build_swing_plan(rep, UseCase.BUY, all_reports={Timeframe.M6: rep})
    assert plan.emerging is None                      # mature scorer used


def test_extended_emerging_vetoes_go():
    # A parabolic IPO: even if geometry would allow it, extended blocks the GO.
    rep = _ipo_report([100, 115, 130, 145, 160])
    plan = build_swing_plan(rep, UseCase.BUY, all_reports={Timeframe.M1: rep})
    assert plan.emerging is not None and plan.emerging.extended
    assert plan.go is False
    assert plan.actionable is False
