"""Intraday reversal-at-support decisions (stockanalyzer.analysis.reversal).

These pin the pure reversal logic the live radar and daytrade_backtest.py both
call: the guard order (at-support → trend/strength → reversal print → geometry →
the ~3% go/no-go aim) across the variant family.
"""
from __future__ import annotations

import pandas as pd

from stockanalyzer.analysis.daycard import BattleZone, DayCard
from stockanalyzer.analysis.reversal import (
    REV_MIN_AIM,
    REVERSAL_VARIANTS,
    ReversalConfig,
    reversal_signal,
)
from stockanalyzer.analysis.signals import Direction

# 2026-06-19 is a Friday (weekday); naive timestamps are treated as Eastern.
_DAY = "2026-06-19"
_REG = pd.Timestamp(f"{_DAY} 11:00")          # inside the regular session


def _card(price=100.0, support=99.7, next_target=104.0, range_pos=10.0,
          day_change_pct=-1.0, supports=None, battle=None, session_low=None):
    supports = [support] if supports is None else supports
    session_low = support if session_low is None else session_low
    battle = [] if battle is None else battle
    return DayCard(
        current_price=price, session_high=round(price * 1.03, 2),
        session_low=session_low, session_range_pct=3.0,
        range_position_pct=range_pos, day_change_pct=day_change_pct,
        bull_pct=50.0, bear_pct=50.0, bias_label="Balanced",
        supports=supports, resistances=[next_target], battle_zones=battle,
        next_target=next_target, next_target_pct=(next_target / price - 1) * 100,
        next_support=support, next_support_pct=(support / price - 1) * 100,
        n_session_bars=20, intraday=True)


def _reversal_bar(low, close, open_):
    """A tiny intraday frame whose LAST bar is the reversal candle under test."""
    idx = pd.date_range(f"{_DAY} 10:40", periods=3, freq="5min")
    return pd.DataFrame({
        "open": [100.5, 100.2, open_], "high": [100.6, 100.3, close + 0.1],
        "low": [100.0, 99.9, low], "close": [100.2, 100.0, close],
        "volume": [1000.0, 1200.0, 1500.0]}, index=idx)


_TOUCH = ReversalConfig("t", "with_trend", "touch")
_CONFIRM = ReversalConfig("c", "with_trend", "confirmed")
_STRONG_TOUCH = ReversalConfig("s", "strong_support", "touch")


def test_touch_fires_at_support_in_uptrend():
    dec = reversal_signal(_card(), None, 100.0, Direction.BULL, 101.0, _REG, _TOUCH)
    assert dec.fired is True
    assert dec.at_support and dec.trend_ok
    assert dec.entry == 100.0
    assert dec.stop < dec.entry < dec.target        # geometry sane
    assert dec.rr > 0
    assert dec.target <= 100.0 * (1 + 0.03) + 1e-9  # ~3% aim, capped under resistance


def test_confirmed_needs_a_reversal_print():
    card = _card()
    # Last bar tags support but closes BELOW it — no reclaim → no fire.
    weak = _reversal_bar(low=99.6, close=99.5, open_=99.9)
    assert reversal_signal(card, weak, 100.0, Direction.BULL, 101.0, _REG,
                           _CONFIRM).fired is False
    # A bullish reclaim of support → fires.
    strong = _reversal_bar(low=99.6, close=100.0, open_=99.8)
    assert reversal_signal(card, strong, 100.0, Direction.BULL, 101.0, _REG,
                           _CONFIRM).fired is True


def test_with_trend_blocked_when_daily_trend_not_up():
    dec = reversal_signal(_card(), None, 100.0, Direction.BEAR, 101.0, _REG, _TOUCH)
    assert dec.fired is False and dec.at_support and not dec.trend_ok
    assert "trend" in dec.reason.lower()


def test_gap_down_blocks_with_trend():
    # day_change_pct=-1 → open≈101.01; prev_close=105 → gap≈−3.8% (a knife).
    dec = reversal_signal(_card(), None, 100.0, Direction.BULL, 105.0, _REG, _TOUCH)
    assert dec.fired is False and dec.gap_pct is not None and dec.gap_pct <= -0.02
    assert "gap" in dec.reason.lower()


def test_strong_support_allows_downtrend_with_confidence():
    # session_low == support and a battle-zone at support → ≥2 confirmations.
    card = _card(battle=[BattleZone(99.7, 40.0)], session_low=99.7)
    dec = reversal_signal(card, None, 100.0, Direction.BEAR, 101.0, _REG, _STRONG_TOUCH)
    assert dec.fired is True and dec.support_conf >= 2


def test_strong_support_rejected_when_confidence_low():
    # Only the single mapped support; session low far away, no battle zone.
    card = _card(session_low=98.0, battle=[])
    dec = reversal_signal(card, None, 100.0, Direction.BEAR, 101.0, _REG, _STRONG_TOUCH)
    assert dec.fired is False and dec.support_conf < 2
    assert "confirmation" in dec.reason.lower()


def test_aim_below_minimum_is_no_go():
    # Resistance only ~1.5% up → capped aim < 2.5% → skip.
    dec = reversal_signal(_card(next_target=101.5), None, 100.0, Direction.BULL,
                          101.0, _REG, _TOUCH)
    assert dec.fired is False and dec.aim_ok is False
    assert "aim" in dec.reason.lower()


def test_no_prev_close_does_not_crash():
    # prev_close=None (gap unknowable) must not raise — the with_trend gate simply
    # can't veto on a gap and falls back to the trend read.
    dec = reversal_signal(_card(), None, 100.0, Direction.BULL, None, _REG, _TOUCH)
    assert dec.fired is True and dec.gap_pct is None


def test_not_fired_high_in_the_day_range():
    dec = reversal_signal(_card(range_pos=60.0), None, 100.0, Direction.BULL,
                          101.0, _REG, _TOUCH)
    assert dec.fired is False and dec.at_support is False


def test_opening_range_freeze_blocks_entry():
    early = pd.Timestamp(f"{_DAY} 09:40")       # inside the first 15 min
    dec = reversal_signal(_card(), None, 100.0, Direction.BULL, 101.0, early, _TOUCH)
    assert dec.fired is False and "opening-range" in dec.reason.lower()


def test_every_variant_produces_sane_geometry_when_it_fires():
    card = _card(battle=[BattleZone(99.7, 40.0)], session_low=99.7)
    bar = _reversal_bar(low=99.6, close=100.0, open_=99.8)
    fired_any = False
    for cfg in REVERSAL_VARIANTS:
        dec = reversal_signal(card, bar, 100.0, Direction.BULL, 101.0, _REG, cfg)
        if dec.fired:
            fired_any = True
            assert dec.stop < dec.entry < dec.target
            assert dec.rr > 0
            assert dec.target / dec.entry - 1 >= REV_MIN_AIM - 1e-9
    assert fired_any            # at least the touch/trend variants fire on this card
