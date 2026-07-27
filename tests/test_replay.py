"""Intraday replay engine (stockanalyzer.replay).

The flagship test here is the no-lookahead invariant: the state frozen at bar *i*
must be identical whether the engine walked to it or whether every later bar was
physically deleted from the input. If anything anywhere in the stack peeked at a
future bar, deleting those bars would change the answer.

All frames are synthetic — no network.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalyzer import replay
from stockanalyzer.analysis import asof, orb, reversal
from stockanalyzer.data.schema import Timeframe

# Six weekday sessions; 2026-06-18 (Thu) is the one we replay. There is history
# before it AND data after it, so a leak would be visible.
_DAYS = [pd.Timestamp(d) for d in
         ("2026-06-11", "2026-06-12", "2026-06-15", "2026-06-16",
          "2026-06-17", "2026-06-18", "2026-06-19")]
_DAY = pd.Timestamp("2026-06-18")
_PREV_CLOSE = 100.0

# 09:30 → 12:30 is enough to straddle the 11:30 ORB cutoff while keeping the
# per-bar indicator cost (5 analyze_timeframe calls) out of the suite's way.
_BARS_PER_DAY = 37


def _day_frame(day: pd.Timestamp, base: float, n: int, freq: str,
               shape=None) -> pd.DataFrame:
    """One session of bars drifting gently up from ``base``."""
    idx = pd.date_range(f"{day:%Y-%m-%d} 09:30", periods=n, freq=freq, tz=asof.ET)
    close = base + np.arange(n) * 0.02
    df = pd.DataFrame({"open": close - 0.05, "high": close + 0.15,
                       "low": close - 0.15, "close": close,
                       "volume": np.full(n, 1000.0)}, index=idx)
    return shape(df) if shape else df


def _gap_day(break_at: int | None = 9) -> callable:
    """Shape the replay day: a +3% gap-up, a tight opening range, then a single
    high-volume break of the OR high at ``break_at`` (index into the session)."""
    def _shape(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # Opening range 09:30–09:45 (bars 0–2): high 103.6, low 102.8.
        df.iloc[0, df.columns.get_loc("open")] = 103.0        # +3% vs prev close
        for i, (hi, lo, cl) in enumerate(((103.6, 102.9, 103.2),
                                          (103.5, 102.8, 103.0),
                                          (103.4, 102.9, 103.1))):
            df.iloc[i, df.columns.get_loc("high")] = hi
            df.iloc[i, df.columns.get_loc("low")] = lo
            df.iloc[i, df.columns.get_loc("close")] = cl
        # Everything after drifts below the OR high...
        for i in range(3, len(df)):
            df.iloc[i, df.columns.get_loc("high")] = 103.3
            df.iloc[i, df.columns.get_loc("low")] = 102.6
            df.iloc[i, df.columns.get_loc("close")] = 103.0
        # ...except one decisive break bar on ~10× volume.
        if break_at is not None:
            df.iloc[break_at, df.columns.get_loc("high")] = 104.5
            df.iloc[break_at, df.columns.get_loc("close")] = 104.2
            df.iloc[break_at, df.columns.get_loc("volume")] = 10_000.0
        return df
    return _shape


def _history(break_at: int | None = 9) -> dict:
    """The (i5, i30, daily, prev_close) bundle precompute_day consumes."""
    i5 = pd.concat([
        _day_frame(d, 100.0 + n * 0.1, _BARS_PER_DAY, "5min",
                   shape=_gap_day(break_at) if d == _DAY else None)
        for n, d in enumerate(_DAYS)])
    i30 = pd.concat([_day_frame(d, 100.0 + n * 0.1, 13, "30min")
                     for n, d in enumerate(_DAYS)])
    # Daily bars that deliberately run PAST the replay day — asof must filter them.
    n = 60
    didx = pd.bdate_range(end="2026-06-19", periods=n)
    step = np.arange(n) * 0.1
    daily = {Timeframe.M1: pd.DataFrame(
        {"open": 95 + step, "high": 96 + step, "low": 94 + step,
         "close": 95.5 + step, "volume": np.full(n, 5e6)}, index=didx)}
    return {"i5": i5, "i30": i30, "daily": daily, "prev_close": _PREV_CLOSE}


def _precompute(hist: dict, **kw) -> replay.ReplaySession:
    return replay.precompute_day(hist["i5"], hist["i30"], hist["daily"], _DAY,
                                 hist["prev_close"], ticker="TEST", **kw)


@pytest.fixture(scope="module")
def hist() -> dict:
    return _history()


@pytest.fixture(scope="module")
def sess(hist) -> replay.ReplaySession:
    return _precompute(hist)


def _fingerprint(bar: replay.ReplayBar) -> tuple:
    """Everything about a bar that a lookahead would perturb.

    The TimeframeReport can't be compared with ``==`` (it holds DataFrames), so it
    is reduced to the properties that would shift if the frame had seen the
    future: its last timestamp, its length, its trend, and its mapped levels.
    """
    d1 = bar.d1
    return (
        bar.i, bar.t, bar.close, bar.setup, bar.kind, bar.go, bar.score, bar.rr,
        bar.entry, bar.stop, bar.target, bar.trigger, bar.actionable,
        bar.earned, bar.denom, bar.lost,
        bar.bots.orb, bar.bots.reversals, bar.bots.swing_go,
        bar.bots.swing_block, bar.bots.contradiction,
        None if d1 is None else (d1.df.index[-1], len(d1.df),
                                 d1.trend.direction,
                                 tuple(round(lv.price, 4) for lv in d1.levels)),
    )


# --------------------------------------------------------------------------- #
# The invariant everything else rests on.
# --------------------------------------------------------------------------- #
def test_bar_state_is_identical_whether_stepped_or_truncated(hist, sess):
    """Delete the future; the past must not move."""
    for i in (0, 5, 12, sess.last):
        t = sess.bars[i].t
        cut = replay.precompute_day(
            hist["i5"][hist["i5"].index <= t],
            hist["i30"][hist["i30"].index <= t],
            hist["daily"], _DAY, hist["prev_close"], ticker="TEST")
        assert cut.bars, f"no bars at i={i}"
        assert _fingerprint(cut.bars[-1]) == _fingerprint(sess.bars[i]), f"bar {i}"


def test_precompute_is_deterministic(hist, sess):
    again = _precompute(hist)
    assert [_fingerprint(b) for b in again.bars] == [_fingerprint(b) for b in sess.bars]


def test_no_bar_carries_data_past_its_own_timestamp(sess):
    for bar in sess.bars:
        assert bar.d1 is not None
        assert bar.d1.df.index[-1] == bar.t
        assert bar.d1.df.index.max() <= bar.t


# --------------------------------------------------------------------------- #
# Session shape.
# --------------------------------------------------------------------------- #
def test_replay_window_starts_after_the_opening_range(sess):
    assert sess.bars[0].clock == replay.REPLAY_START
    assert all(b.t.strftime("%H:%M") >= replay.REPLAY_START for b in sess.bars)
    assert [b.i for b in sess.bars] == list(range(len(sess.bars)))


def test_opening_range_is_measured_from_the_completed_first_15_minutes(sess):
    assert sess.orange is not None
    assert sess.orange.high == 103.6 and sess.orange.low == 102.8
    assert sess.orange.gap_up is True
    assert sess.orange.gap_pct == pytest.approx(0.03)


def test_session_full_is_the_whole_day_not_the_replay_window(sess):
    """The reveal frame holds the open too — it is the only thing allowed to."""
    assert len(sess.session_full) == _BARS_PER_DAY
    assert len(sess.bars) < len(sess.session_full)


def test_replayable_days_drops_the_earliest_session():
    """The first day in the window has no prior session, so its context and
    prev_close are degraded — don't offer it."""
    assert replay.replayable_days({"days": _DAYS}) == _DAYS[1:]


# --------------------------------------------------------------------------- #
# Bots.
# --------------------------------------------------------------------------- #
def test_orb_fires_on_the_volume_break_inside_the_window(sess):
    fired = [b for b in sess.bars if b.bots.orb.fired]
    assert fired, "the break bar should have fired"
    b = fired[0]
    assert b.t.strftime("%H:%M") <= "11:30"
    assert b.bots.orb.rvol > orb.MIN_BREAKOUT_RVOL


def test_orb_geometry_matches_the_orb_rules(sess):
    b = next(b for b in sess.bars if b.bots.orb.fired)
    g = b.bots.orb
    assert g.stop == round(sess.orange.low * (1 - orb.ORB_STOP_BUFFER), 4)
    assert g.target == pytest.approx(g.entry + orb.ORB_TARGET_R * (g.entry - g.stop))
    assert g.rr == pytest.approx(orb.ORB_TARGET_R, abs=0.01)


def test_orb_never_fires_on_a_break_after_the_window_closes(hist):
    """Same gap, same volume surge — but at 12:00, past the 11:30 cutoff."""
    late = _precompute(_history(break_at=31))       # 09:30 + 31×5min = 12:05
    assert not any(b.bots.orb.fired for b in late.bars)
    outside = [b for b in late.bars if not b.bots.orb.in_window]
    assert outside and "window" in outside[-1].bots.orb.reason


def test_every_bar_evaluates_every_reversal_variant(sess):
    names = {cfg.name for cfg in reversal.REVERSAL_VARIANTS}
    for bar in sess.bars:
        assert len(bar.bots.reversals) == len(reversal.REVERSAL_VARIANTS)
        assert {d.variant for d in bar.bots.reversals} == names


def test_a_non_go_bar_always_explains_which_gate_blocked_it(sess):
    """A blank verdict teaches nothing — every stand-aside carries its reason."""
    for bar in sess.bars:
        if bar.go:
            assert bar.bots.swing_block == ""
        else:
            assert bar.bots.swing_block, f"bar {bar.clock} blocked with no reason"


def test_fired_bars_index_matches_the_verdicts(sess):
    assert sess.fired_bars == tuple(b.i for b in sess.bars if b.bots.any_fired)


def test_blocked_digest_reports_the_modal_gate(sess):
    reason, n, total = replay.blocked_digest(sess)
    assert total == len(sess.bars)
    if reason:
        assert 0 < n <= total
        assert reason in {b.bots.swing_block for b in sess.bars}


# --------------------------------------------------------------------------- #
# Judging.
# --------------------------------------------------------------------------- #
def _bars(rows) -> pd.DataFrame:
    idx = pd.date_range("2026-06-18 10:00", periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["high", "low", "close"], index=idx)


def test_judge_intraday_resolves_an_ambiguous_bar_stop_first():
    """One bar touching both levels is scored as the loss — the conservative read,
    since the tape can't tell us which came first."""
    rest = _bars([(102.5, 98.5, 101.0)])
    assert replay.judge_intraday(rest, 100.0, 99.0, 102.0, 2.0) == ("stop", -1.0)


def test_judge_intraday_target_pays_the_rr_floor():
    rest = _bars([(100.5, 99.8, 100.2), (102.4, 100.1, 102.2)])
    assert replay.judge_intraday(rest, 100.0, 99.0, 102.0, 2.0) == ("target", 2.0)


def test_judge_intraday_falls_back_to_the_closing_price():
    rest = _bars([(100.5, 99.8, 100.2), (100.9, 99.9, 100.5)])
    status, r = replay.judge_intraday(rest, 100.0, 99.0, 102.0, 2.0)
    assert status == "eod" and r == pytest.approx(0.5)


def test_judge_intraday_with_no_remaining_bars_is_still_open():
    assert replay.judge_intraday(_bars([]), 100.0, 99.0, 102.0, 2.0) == ("open", 0.0)


# --------------------------------------------------------------------------- #
# R accounting.
# --------------------------------------------------------------------------- #
def test_trade_r_measures_against_the_original_stop():
    """A stop trailed to breakeven must not make R blow up — that's what
    init_stop is for."""
    row = {"entry": 100.0, "init_stop": 98.0, "stop": 100.0, "exit_price": 104.0}
    assert replay.trade_r(row) == 2.0


def test_trade_r_is_none_without_a_usable_exit_or_risk():
    assert replay.trade_r({"entry": 100.0, "init_stop": 98.0}) is None
    assert replay.trade_r({"entry": 100.0, "init_stop": 100.0,
                           "exit_price": 104.0}) is None
