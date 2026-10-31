"""Context-based intraday setups (stockanalyzer.setups) and their harness.

Synthetic 5-min sessions pin each setup's geometry, show it stays out when a
rule fails, and prove the harness never shows a setup a bar from the future.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from stockanalyzer.setups import SETUPS, base_breakout, double_bottom, harness, silver_bullet
from stockanalyzer.setups.base import (STOP_BUFFER, SetupContext, SetupDecision,
                                       daily_bias_up, fire)

ET = "America/New_York"
DAY, PREV = "2026-10-20", "2026-10-19"


def _flat(day, price, n=78, start="09:30", rng=0.1):
    idx = pd.date_range(f"{day} {start}", periods=n, freq="5min", tz=ET)
    return pd.DataFrame({"open": price, "high": price + rng, "low": price - rng,
                         "close": price, "volume": 1000.0}, index=idx)


def _set(df, rows: dict):
    for i, (o, h, l, c) in rows.items():
        df.iloc[i, :4] = [o, h, l, c]
    return df


def _daily(up=True, n=30, end=PREV):
    idx = pd.date_range(end=end, periods=n, freq="B")
    closes = np.linspace(80, 101, n) if up else np.linspace(120, 101, n)
    return pd.DataFrame({"open": closes, "high": closes + 1, "low": closes - 1,
                         "close": closes, "volume": 1e6}, index=idx)


def _prev_session():
    prev = _flat(PREV, 101.0)
    prev.iloc[10, 1] = 104.0                         # yesterday's high
    prev.iloc[60, 1] = 103.0                         # yesterday's 13:30+ high
    return prev


def _sb_session():
    """Sell-off to 96, displacement up leaves an FVG [97.0, 97.6] (CE 97.3) at
    10:00, the 10:10 bar retraces into the CE, then price runs to 99.4."""
    s = _flat(DAY, 99.3)
    return _set(s, {
        0: (99.0, 99.2, 98.6, 98.7), 1: (98.7, 98.8, 97.8, 97.9),
        2: (97.9, 98.0, 96.5, 96.6), 3: (96.6, 96.8, 96.0, 96.4),
        4: (96.4, 97.0, 96.3, 96.9),                 # bar 1 (high 97.0)
        5: (96.95, 98.9, 96.75, 98.8),               # bar 2: displacement up
        6: (98.8, 98.95, 97.6, 98.7),                # bar 3 (low 97.6) -> FVG
        7: (98.7, 99.0, 97.8, 98.9),                 # stays above the CE
        8: (98.9, 98.95, 97.25, 97.6),               # retrace fills the CE
        9: (97.6, 98.5, 97.5, 98.4), 10: (98.4, 99.4, 98.3, 99.3),
    })


def _db_session(void=False):
    """Base 100 ± 0.1, a sprint down to P1 97.0 at 10:55, a bounce to a 98.2
    neckline, P2 97.1 at 11:20, a neckline break at 11:30, then 99.6."""
    s = _flat(DAY, 99.5)
    s.iloc[:11, :4] = [100.0, 100.1, 99.9, 100.0]
    _set(s, {
        11: (100.0, 100.1, 99.6, 99.7), 12: (99.7, 99.75, 99.1, 99.2),
        13: (99.2, 99.25, 98.5, 98.6), 14: (98.6, 98.65, 98.0, 98.1),
        15: (98.1, 98.15, 97.5, 97.6), 16: (97.6, 97.65, 97.2, 97.3),
        17: (97.3, 97.35, 97.0, 97.2),               # P1
        18: (97.2, 97.8, 97.15, 97.7), 19: (97.7, 98.2, 97.6, 98.1),   # neckline 98.2
        20: (98.1, 98.15, 97.9, 97.95), 21: (97.95, 98.0, 97.6, 97.65),
        22: (97.65, 97.7, 96.0 if void else 97.1, 97.5),               # P2
        23: (97.5, 97.9, 97.4, 97.85),
        24: (97.9, 98.3, 97.8, 98.25),               # neckline break
        25: (98.25, 99.6, 98.2, 99.5),
    })
    s.iloc[26:, :4] = [99.5, 99.6, 99.4, 99.5]
    return s


def _bb_session(flush_bar=31, trigger_bar=66):
    """Open 100, a sell-off to 94.6, a flush low of 94.0 at ``flush_bar`` (12:05), a
    base of 94.2-95.2 with higher lows, a 15:00 close of 95.6 above its high, then a
    run to 97.7 that clears 1R (96.59) and 2R (97.59) and goes flat."""
    s = _flat(DAY, 97.7)
    for i in range(flush_bar):
        a, b = 100 - 5.4 * i / flush_bar, 100 - 5.4 * (i + 1) / flush_bar
        s.iloc[i, :4] = [a, a + 0.05, b - 0.05, b]
    s.iloc[flush_bar, :4] = [94.6, 94.7, 94.0, 94.4]
    mid = (flush_bar + 1 + trigger_bar) // 2
    for i in range(flush_bar + 1, trigger_bar):
        s.iloc[i, :4] = [94.7, 95.0, 94.2 if i < mid else 94.3, 94.7]
    s.iloc[(flush_bar + trigger_bar) // 2, 1] = 95.2          # the base high
    s.iloc[trigger_bar, :4] = [94.9, 95.7, 94.85, 95.6]       # closes above it
    s.iloc[trigger_bar + 1, :4] = [95.6, 96.8, 95.55, 96.7]   # clears 1R
    s.iloc[trigger_bar + 2, :4] = [96.7, 97.8, 96.6, 97.7]    # clears 2R
    return s


def _bb(i=0):
    return [(base_breakout, base_breakout.VARIANTS[i])]


def _bb_sig(session, upto=67, cfg=None):
    """The decision as of the bar before ``upto`` (67 -> the 15:00 trigger bar)."""
    ctx = SetupContext(session.iloc[:upto], None, None, None, None)
    return base_breakout.signal(ctx, cfg or base_breakout.VARIANTS[0])


def _hist(session, daily=None):
    pre = _flat(DAY, 99.5, n=66, start="04:00")
    pre.iloc[5, 1] = 100.5                           # pre-market high
    i5 = pd.concat([_prev_session(), pre, session]).sort_index()
    return {"i5": i5, "daily": daily if daily is not None else _daily(),
            "days": [pd.Timestamp(PREV), pd.Timestamp(DAY)]}


def _sb():
    return [(silver_bullet, silver_bullet.VARIANTS[0])]


def _db():
    return [(double_bottom, double_bottom.VARIANTS[0])]


# --------------------------------------------------------------------------- #
def test_registry_contract():
    names = [cfg.name for m in SETUPS.values() for cfg in m.VARIANTS]
    assert len(names) == len(set(names))             # variant names are cohort ids
    for name, mod in SETUPS.items():
        assert mod.NAME == name and mod.VARIANTS and callable(mod.signal)


def test_fire_refuses_short_targets_but_accepts_an_exact_1r():
    cfg = silver_bullet.VARIANTS[0]
    assert not fire(cfg, "x", "", 10.0, 9.5, 10.4).fired
    exact = fire(cfg, "x", "", 98.2, 96.903, 98.2 + (98.2 - 96.903))
    assert exact.fired and exact.rr == 1.0


def test_daily_bias():
    assert daily_bias_up(_daily(up=True)) is True
    assert daily_bias_up(_daily(up=False)) is False
    assert daily_bias_up(_daily(n=5)) is None


def test_silver_bullet_fills_the_ce_and_aims_at_the_nearest_liquidity():
    [t] = harness.run_day(_hist(_sb_session()), 1, _sb())
    assert t["t"] == "10:10"
    assert t["entry"] == pytest.approx(97.3)
    assert t["stop"] == pytest.approx(96.75 * (1 - STOP_BUFFER), abs=1e-4)
    assert t["target"] == pytest.approx(99.2)        # session high when the gap formed
    assert t["status"] == "target" and t["gross_r"] == pytest.approx(t["rr"])


def test_silver_bullet_stands_aside_without_a_bullish_daily_bias():
    assert harness.run_day(_hist(_sb_session(), _daily(up=False)), 1, _sb()) == []


def test_silver_bullet_cancels_an_order_unfilled_by_10_30():
    s = _sb_session()
    s.iloc[8:14, :4] = [98.9, 99.0, 98.8, 98.9]      # no retrace until ...
    s.iloc[14, :4] = [98.9, 98.95, 97.2, 97.5]       # ... the 10:40 bar
    assert harness.run_day(_hist(s), 1, _sb()) == []


def test_double_bottom_buys_the_neckline_break_with_a_1r_target():
    [t] = harness.run_day(_hist(_db_session()), 1, _db())
    assert t["t"] == "11:30"
    assert t["entry"] == pytest.approx(98.2)
    assert t["stop"] == pytest.approx(97.0 * (1 - STOP_BUFFER), abs=1e-4)
    assert t["rr"] == 1.0 and t["status"] == "target"


def test_double_bottom_is_void_when_the_second_low_undercuts():
    assert harness.run_day(_hist(_db_session(void=True)), 1, _db()) == []


def test_base_breakout_buys_the_close_above_a_flush_base_and_stops_mid_base():
    both = [(base_breakout, v) for v in base_breakout.VARIANTS]
    t1, t2 = harness.run_day(_hist(_bb_session()), 1, both)
    stop = (95.2 + 94.2) / 2 * (1 - STOP_BUFFER)             # base midpoint, not its low
    for t in (t1, t2):
        assert t["t"] == "15:00" and t["status"] == "target"
        assert t["entry"] == pytest.approx(95.6)
        assert t["stop"] == pytest.approx(stop, abs=1e-4)
    assert (t1["variant"], t1["rr"]) == ("pm-base-brk-1r", 1.0)
    assert (t2["variant"], t2["rr"]) == ("pm-base-brk-2r", 2.0)
    assert t1["target"] == pytest.approx(95.6 + (95.6 - stop), abs=1e-3)
    assert t2["target"] == pytest.approx(95.6 + 2 * (95.6 - stop), abs=1e-3)


@pytest.mark.parametrize("make, upto, why", [
    (lambda: _bb_session(), 66, "waiting for a close above the base high"),
    (lambda: _bb_session(trigger_bar=48), 49, "outside the breakout window"),
    (lambda: _bb_session(flush_bar=37, trigger_bar=54), 55, "base only 16 bars"),
])
def test_base_breakout_stands_aside_before_the_window_the_base_or_the_close(make, upto, why):
    dec = _bb_sig(make(), upto)
    assert not dec.fired and why in dec.reason


def test_base_breakout_needs_a_real_flush_by_13_00():
    shallow = _bb_session()
    shallow.iloc[:, :4] = 100 - (100 - shallow.iloc[:, :4]) * 0.3   # same shape, -1.8% not -6%
    assert "no flush" in _bb_sig(shallow).reason
    still_falling = _bb_session()
    still_falling.iloc[58, 2] = 93.5                          # a new low at 14:20
    assert "still making lows" in _bb_sig(still_falling).reason


def test_base_breakout_rejects_a_wide_base_and_a_base_of_lower_lows():
    wide = _bb_session()
    wide.iloc[40, 1] = 98.5
    assert "base range" in _bb_sig(wide).reason
    sagging = _bb_session()
    sagging.iloc[49:66, 2] = 94.1                             # second half under the first
    assert "lower lows" in _bb_sig(sagging).reason


def test_base_breakout_does_not_chase_a_second_breakout_close():
    s = _bb_session()
    s.iloc[70, :4] = [97.7, 98.5, 97.6, 98.4]                 # a higher close at 15:20
    loose = replace(base_breakout.VARIANTS[0], max_base_range=0.10)
    assert _bb_sig(s, 67, loose).fired                        # the 15:00 breakout fires
    chase = _bb_sig(s, 71, loose)
    assert not chase.fired and "already broke out at 15:00" in chase.reason


def test_future_bars_never_change_the_entry():
    """Garbage after the fire bar must not move when or where a setup fires; the
    daily row for today (unknown at the open) must not reach the bias either."""
    for session, variants in ((_sb_session(), _sb()), (_db_session(), _db()),
                              (_bb_session(), _bb())):
        [base] = harness.run_day(_hist(session), 1, variants)
        j = list(session.index.strftime("%H:%M")).index(base["t"])
        noisy = session.copy()
        rng = np.random.default_rng(0)
        noisy.iloc[j + 1:, :4] = rng.uniform(1, 500, size=(len(noisy) - j - 1, 4))
        crash_today = pd.concat([_daily(), pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
            index=[pd.Timestamp(DAY)])])
        [other] = harness.run_day(_hist(noisy, crash_today), 1, variants)
        for key in ("t", "entry", "stop", "target"):
            assert other[key] == base[key]


def test_a_resting_order_stopped_on_its_fill_bar_is_a_full_loss():
    s = _flat(DAY, 100.0)
    s.iloc[5, 2] = 98.0                              # the fill bar's low pierces the stop
    dec = SetupDecision(True, "", "x", "x", entry=99.5, stop=99.0, target=100.5,
                        rr=2.0, filled_intrabar=True)
    out = harness.judge(dec, s, 5, spread=0.0)
    assert out["status"] == "stop" and out["gross_r"] == -1.0


def test_random_entry_null_is_seeded_and_keeps_the_geometry():
    hist = _hist(_db_session())
    trades = pd.DataFrame(harness.run_day(hist, 1, _db(), ticker="X"))
    a = harness.random_entry_null({"X": hist}, trades, reps=5)
    b = harness.random_entry_null({"X": hist}, trades, reps=5)
    assert len(a) == 5 and (a == b).all()
    assert ((a >= -1.0 - 1) & (a <= trades["rr"].iloc[0])).all()   # within −1R−cost .. +1R


def test_signal_is_a_pure_function_of_the_context():
    s = _db_session()
    ctx = SetupContext(s.iloc[:25], None, None, None, None)
    a = double_bottom.signal(ctx, double_bottom.VARIANTS[0])
    b = double_bottom.signal(ctx, double_bottom.VARIANTS[0])
    assert a == b and a.fired and a.filled_intrabar
