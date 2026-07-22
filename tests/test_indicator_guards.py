"""The famous-indicator guards (Bollinger blowoff + ADX/DMI trend) that veto the
worst radar decisions: don't chase a 2σ spike, don't go long into a strong
downtrend. Mirrors the SPCX / NOK failures from the paper-trade export.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.analysis.indicators import add_indicators
from stockanalyzer.data.schema import Timeframe, validate_ohlcv
from stockanalyzer.explain.swing import (_long_geometry, _macd_against,
                                         _short_geometry, build_swing_plan)
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.strategy import SWING_PACE, SwingPace

_FAST = SWING_PACE[SwingPace.FAST]


def _frame(closes: list[float]) -> pd.DataFrame:
    c = np.array(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + 0.3
    l = np.minimum(o, c) - 0.3
    idx = pd.date_range("2026-01-01", periods=len(c), freq="B")
    return validate_ohlcv(pd.DataFrame(
        {"open": o, "high": h, "low": l, "close": c, "volume": np.full(len(c), 1e6)},
        index=idx))


# --------------------------------------------------------------------------- #
# Indicator math.
# --------------------------------------------------------------------------- #
def test_indicators_are_wired_into_add_indicators():
    df = add_indicators(_frame(list(100 + 0.5 * np.arange(40))))
    for col in ("bb_upper", "bb_lower", "bb_mid", "adx", "plus_di", "minus_di"):
        assert col in df.columns


def test_adx_direction_matches_trend():
    up = add_indicators(_frame(list(100 + 0.6 * np.arange(40))))
    down = add_indicators(_frame(list(100 - 0.6 * np.arange(40))))
    assert up["plus_di"].iloc[-1] > up["minus_di"].iloc[-1]
    assert down["minus_di"].iloc[-1] > down["plus_di"].iloc[-1]
    assert up["adx"].iloc[-1] >= 25 and down["adx"].iloc[-1] >= 25   # both strong trends


def test_bollinger_flags_sharp_spike_only():
    base = list(100 + 0.05 * np.arange(30))
    spike = add_indicators(_frame(base + [105, 118]))           # vertical 2-day pop
    calm = add_indicators(_frame(base + [100.0, 100.1]))
    assert spike["close"].iloc[-1] > spike["bb_upper"].iloc[-1]
    assert calm["close"].iloc[-1] <= calm["bb_upper"].iloc[-1]


# --------------------------------------------------------------------------- #
# Swing-plan vetoes (the decisions these would have prevented).
# --------------------------------------------------------------------------- #
def test_strong_downtrend_vetoes_long_go():
    # NOK class: a strong, established downtrend — never a long GO.
    rep = analyze_timeframe(_frame(list(100 - 0.7 * np.arange(45))))
    plan = build_swing_plan(rep, UseCase.BUY, SwingPace.FAST,
                            all_reports={Timeframe.M6: rep})
    assert plan.go is False
    names = " ".join(c.name for c in plan.checks)
    assert "ADX" in names                                       # the guard is shown


def test_blowoff_spike_is_not_actionable_go():
    # SPCX class on an established name: a 2σ blowoff close is not a chase-it GO.
    base = list(100 + 0.05 * np.arange(35))
    rep = analyze_timeframe(_frame(base + [108, 120]))
    plan = build_swing_plan(rep, UseCase.BUY, SwingPace.FAST,
                            all_reports={Timeframe.M6: rep})
    assert plan.go is False


def test_healthy_uptrend_not_vetoed():
    # MU class: a steady, orderly uptrend must still be allowed to score well and
    # is NOT killed by the guards (ADX agrees, price inside the band).
    rep = analyze_timeframe(_frame(list(100 + 0.4 * np.arange(60))))
    plan = build_swing_plan(rep, UseCase.BUY, SwingPace.FAST,
                            all_reports={Timeframe.M6: rep})
    bb_rows = [c for c in plan.checks if "Bollinger" in c.name]
    adx_rows = [c for c in plan.checks if "ADX" in c.name]
    assert bb_rows and bb_rows[0].ok                            # inside the band
    assert adx_rows and adx_rows[0].ok                          # trend agrees


# --------------------------------------------------------------------------- #
# Throwback entry + leg-spent gate (your point from today's trades): when the
# wait makes you buy a move that's already spent, the breakout pays scraps or
# fails (NOK +11.9% room → stopped; MSFT → stopped). The geometry now buys the
# retest of the wall (not the spike) and refuses to arm a spent leg.
# --------------------------------------------------------------------------- #
def test_breakout_entry_is_the_retest_not_the_spike():
    # No room to the first wall (101, +1%), reachable next wall (105). The entry
    # must sit AT the breakout level (the retest), not buffered above it.
    geom = _long_geometry(100.0, [101.0, 105.0], [96.0], atr_abs=3.0,
                          budget_atr_abs=3.0, tuning=_FAST, countertrend=False)
    assert geom.kind == "breakout_wait"
    assert geom.entry == 101.0                          # retest of the wall, no chase buffer
    assert geom.trigger == 101.0
    assert "retest" in geom.note


def test_spent_leg_becomes_no_trade_not_breakout():
    # Same geometry, but the daily close is 2σ-extended (leg_spent) — the NOK/MSFT
    # case. Chasing the break here buys the top, so it must collapse to no_trade.
    geom = _long_geometry(100.0, [101.0, 105.0], [96.0], atr_abs=3.0,
                          budget_atr_abs=3.0, tuning=_FAST, countertrend=False,
                          leg_spent=True)
    assert geom.kind == "no_trade"
    assert geom.trigger is None
    assert "spent" in geom.note


def test_short_side_mirrors_throwback_and_gate():
    ok = _short_geometry(100.0, [104.0], [99.0, 95.0], atr_abs=3.0,
                         budget_atr_abs=3.0, tuning=_FAST, countertrend=False)
    assert ok.kind == "breakout_wait" and ok.entry == 99.0 and "retest" in ok.note
    spent = _short_geometry(100.0, [104.0], [99.0, 95.0], atr_abs=3.0,
                            budget_atr_abs=3.0, tuning=_FAST, countertrend=False,
                            leg_spent=True)
    assert spent.kind == "no_trade" and "spent" in spent.note


def test_plan_surfaces_retest_guidance_on_breakout():
    # End-to-end: a coiled-under-wall breakout plan tells the user to buy the
    # retest, not chase the spike.
    closes = list(100 + 0.05 * np.arange(40))           # calm, not extended
    rep = analyze_timeframe(_frame(closes))
    plan = build_swing_plan(rep, UseCase.BUY, SwingPace.FAST,
                            all_reports={Timeframe.M6: rep})
    if plan.kind == "breakout_wait":
        assert "retest" in plan.guidance


# --------------------------------------------------------------------------- #
# Daily-MACD momentum veto — the cleanest winner/loser separator in the book:
# losers were entered with the daily MACD below its signal. A long whose daily
# MACD line disagrees never earns a GO.
# --------------------------------------------------------------------------- #
def _rollover():
    # Uptrend that rolls over at the end → daily MACD crosses below its signal.
    closes = list(100 + 0.4 * np.arange(50)) + [120 - 1.2 * i for i in range(1, 11)]
    return analyze_timeframe(_frame(closes))


def test_macd_against_vetoes_a_falling_knife_not_a_clean_trend():
    up = analyze_timeframe(_frame(list(100 + 0.4 * np.arange(60))))
    against, note = _macd_against({Timeframe.M6: up}, up.df, short=False)
    assert against is False and note is None            # clean uptrend: hist ≥ 0

    roll = _rollover()                                   # uptrend then sustained drop
    against, note = _macd_against({Timeframe.M6: roll}, roll.df, short=False)
    assert against is True and "MACD" in note           # hist negative AND still falling


def test_macd_veto_lets_a_stabilizing_buy_the_dip_through():
    # A healthy uptrend with a shallow dip that's ALREADY turning back up: the
    # histogram is still negative but RISING, so this is not a falling knife and
    # must NOT be vetoed — exactly the with-trend pullback the engine wants.
    dip_recover = analyze_timeframe(_frame(
        list(100 + 0.5 * np.arange(50)) + [124, 122.5, 121.5, 122.0, 123.5, 125.0]))
    against, note = _macd_against({Timeframe.M6: dip_recover}, dip_recover.df,
                                  short=False)
    assert against is False and note is None

    # …but a dip that is STILL dropping (knife mid-fall) is vetoed.
    dip_falling = analyze_timeframe(_frame(
        list(100 + 0.5 * np.arange(50)) + [124, 122, 120, 118, 116]))
    against, _ = _macd_against({Timeframe.M6: dip_falling}, dip_falling.df, short=False)
    assert against is True


def test_macd_veto_noops_without_a_daily_frame():
    bare = _frame(list(100 + 0.4 * np.arange(40)))       # raw OHLCV, no indicators
    assert _macd_against(None, bare, short=False) == (False, None)


def test_macd_falling_knife_does_not_become_a_go():
    # End-to-end: a rolled-over name (daily MACD histogram negative and falling)
    # is never a long GO. (The MACD note text itself is asserted in the unit test
    # above; here the honest guidance is 'No valid setup' since the knife has none.)
    roll = _rollover()
    plan = build_swing_plan(roll, UseCase.BUY, SwingPace.FAST,
                            all_reports={Timeframe.M6: roll})
    assert plan.go is False and plan.actionable is False
