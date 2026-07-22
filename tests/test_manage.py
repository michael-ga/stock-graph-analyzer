"""Managed exit engine (manage.assess_position) — the two adaptive behaviors."""
from __future__ import annotations

from stockanalyzer import manage
from stockanalyzer.analysis.signals import Direction
from stockanalyzer.analysis.trend_change import TrendChange


def _pos(entry=100.0, stop=98.0, init_stop=None):
    return {"entry": entry, "stop": stop, "init_stop": init_stop or stop}


def test_breakeven_at_1R_is_spread_adjusted():
    # entry 100, init_stop 98 → R = 2. +1R is price 102.
    acts = manage.assess_position(_pos(100.0, 98.0), 102.0, None, None, None, 0.0)
    be = [a for a in acts if a.kind == "move_stop_breakeven"]
    assert be, "breakeven should fire at +1R"
    assert be[0].new_stop == manage.breakeven_stop(100.0) == round(100.0 + 0.02, 4)
    assert abs(be[0].r_multiple - 1.0) < 1e-9


def test_no_breakeven_below_1R():
    acts = manage.assess_position(_pos(100.0, 98.0), 101.0, None, None, None, 0.0)
    assert not any(a.kind == "move_stop_breakeven" for a in acts)


def test_R_uses_init_stop_not_current_stop():
    # current stop already ratcheted to 99, but R must use the ORIGINAL 98.
    pos = {"entry": 100.0, "init_stop": 98.0, "stop": 99.0}
    acts = manage.assess_position(pos, 102.0, None, None, None, 0.0)
    be = [a for a in acts if a.kind == "move_stop_breakeven"]
    assert be and abs(be[0].r_multiple - 1.0) < 1e-9      # +1R by init_stop, not +4R


def test_trail_rides_ema8_when_in_profit():
    pos = {"entry": 100.0, "init_stop": 98.0, "stop": 100.5}   # +2R at 104
    acts = manage.assess_position(pos, 104.0, None, None, None, 0.0, ema8_5m=103.0)
    trail = [a for a in acts if a.kind == "trail_stop"]
    assert trail and trail[0].new_stop > 100.5


def test_never_widens_a_stop():
    # current stop 103 already above any candidate (ema8 trail ~100.9) → no action.
    pos = {"entry": 100.0, "init_stop": 98.0, "stop": 103.0}
    acts = manage.assess_position(pos, 104.0, None, None, None, 0.0, ema8_5m=101.0)
    assert not any(a.kind == "trail_stop" for a in acts)
    assert all(a.new_stop is None or a.new_stop > 103.0 for a in acts)


def test_trend_flip_tightens_does_not_dump():
    tc = TrendChange(likely=True, direction=Direction.BEAR, score=0.7)
    pos = {"entry": 100.0, "init_stop": 98.0, "stop": 98.5}
    acts = manage.assess_position(pos, 100.5, None, None, tc, 0.0)
    t = [a for a in acts if a.kind == "trend_test_tighten"]
    assert t, "a confident bear flip should tighten the stop"
    assert t[0].new_stop > 98.5            # ratcheted up to test the move
    assert t[0].fraction is None           # NOT a market dump
    assert not any(a.fraction == 1.0 for a in acts)


def test_trend_flip_below_confidence_does_nothing():
    tc = TrendChange(likely=True, direction=Direction.BEAR, score=0.5)   # < 0.6
    acts = manage.assess_position(_pos(100.0, 98.0), 100.5, None, None, tc, 0.0)
    assert not any(a.kind == "trend_test_tighten" for a in acts)


def test_ema8_break_tightens():
    pos = {"entry": 100.0, "init_stop": 98.0, "stop": 98.5}
    acts = manage.assess_position(pos, 100.5, None, None, None, 0.0,
                                  ema8_5m=101.0, intraday_close=100.4)  # closed < EMA8
    assert any(a.kind == "trend_test_tighten" for a in acts)


def test_weekend_flat_unless_held():
    pos = _pos(100.0, 98.0)
    acts = manage.assess_position(pos, 101.0, None, None, None, 0.0, is_friday_flat=True)
    assert acts and acts[0].kind == "weekend_flat" and acts[0].fraction == 1.0
    held = manage.assess_position(pos, 101.0, None, None, None, 0.0,
                                  is_friday_flat=True, hold_weekend=True)
    assert not any(a.kind == "weekend_flat" for a in held)
