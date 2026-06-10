"""Tests for live flip detection (pure, no Streamlit)."""
from __future__ import annotations

from stockanalyzer.live_events import Event, LiveState, diff_states


def test_no_prev_state_is_silent():
    cur = LiveState(swing_go=True, rr=3.0)
    assert diff_states(None, cur) == []


def test_no_change_emits_nothing():
    s = LiveState(light="go", swing_go=True, rr=3.0, preset_key="lean_buy",
                  signals=frozenset({"hammer"}), price=10.0)
    assert diff_states(s, s) == []


def test_swing_go_flip_on():
    prev = LiveState(swing_go=False, rr=1.5, light="forming")
    cur = LiveState(swing_go=True, rr=2.6, light="go")
    kinds = {e.kind for e in diff_states(prev, cur)}
    assert "swing" in kinds
    swing = next(e for e in diff_states(prev, cur) if e.kind == "swing")
    assert swing.severity == "good" and "2.6" in swing.text


def test_swing_go_flip_off_is_warn():
    prev = LiveState(swing_go=True, rr=2.2, light="go")
    cur = LiveState(swing_go=False, rr=1.4, light="forming")
    swing = next(e for e in diff_states(prev, cur) if e.kind == "swing")
    assert swing.severity == "warn"


def test_new_and_gone_signals():
    prev = LiveState(signals=frozenset({"hammer"}))
    cur = LiveState(signals=frozenset({"hammer", "bearish_engulfing"}))
    evs = diff_states(prev, cur)
    assert any(e.kind == "signal_new" and "bearish_engulfing" in e.text for e in evs)
    evs2 = diff_states(cur, prev)
    assert any(e.kind == "signal_gone" and "bearish_engulfing" in e.text for e in evs2)


def test_trend_change_crossing():
    prev = LiveState(trend_change_likely=False)
    cur = LiveState(trend_change_likely=True, trend_change_dir="bull")
    ev = next(e for e in diff_states(prev, cur) if e.kind == "trend_change")
    assert "bull" in ev.text and ev.severity == "warn"


def test_stop_and_target_touches():
    prev = LiveState(price=10.0, stop=9.5, target=11.0)
    # price falls through the stop
    down = diff_states(prev, LiveState(price=9.4, stop=9.5, target=11.0))
    assert any(e.kind == "stop" and e.severity == "bad" for e in down)
    # price rises through the target
    up = diff_states(prev, LiveState(price=11.2, stop=9.5, target=11.0))
    assert any(e.kind == "target" and e.severity == "good" for e in up)


def test_no_level_touch_when_price_stays_between():
    prev = LiveState(price=10.0, stop=9.5, target=11.0)
    cur = LiveState(price=10.4, stop=9.5, target=11.0)
    kinds = {e.kind for e in diff_states(prev, cur)}
    assert "stop" not in kinds and "target" not in kinds
