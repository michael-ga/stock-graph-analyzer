"""Point-in-time frame reconstruction (stockanalyzer.analysis.asof).

These pin the no-lookahead contract every backtest and the interactive replay
rest on: a frame built "as of" bar t must contain nothing after t, and today's
daily bar must be the *partial* bar the live radar sees intraday — never the
completed one.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalyzer.analysis import asof
from stockanalyzer.data.schema import Timeframe

# Wed 2026-06-17 → Fri 2026-06-19: three weekday sessions in a row.
_DAYS = ["2026-06-17", "2026-06-18", "2026-06-19"]


def _intraday(freq: str = "5min", per_day: int = 78) -> pd.DataFrame:
    """Regular-session bars (09:30 →) for three consecutive weekdays, ET-aware."""
    frames = []
    for d, base in zip(_DAYS, (100.0, 101.0, 102.0)):
        idx = pd.date_range(f"{d} 09:30", periods=per_day, freq=freq,
                            tz=asof.ET)
        step = np.arange(per_day) * 0.01
        frames.append(pd.DataFrame({
            "open": base + step, "high": base + step + 0.3,
            "low": base + step - 0.3, "close": base + step + 0.1,
            "volume": np.full(per_day, 1000.0),
        }, index=idx))
    return pd.concat(frames)


def _daily(n: int = 60) -> pd.DataFrame:
    """Completed daily bars ending the day *before* the middle test day."""
    idx = pd.bdate_range(end="2026-06-17", periods=n)
    step = np.arange(n) * 0.5
    return pd.DataFrame({"open": 50 + step, "high": 51 + step, "low": 49 + step,
                         "close": 50.5 + step, "volume": np.full(n, 5e6)}, index=idx)


def test_asof_intraday_never_reaches_past_t():
    df = _intraday()
    for t in (df.index[5], df.index[100], df.index[-1]):
        win = asof.asof_intraday(df, t, days=2)
        assert win.index.max() == t
        assert win.index.min() > t - pd.Timedelta(days=2)


def test_session_upto_grows_one_bar_at_a_time():
    df = _intraday()
    day = pd.Timestamp(_DAYS[1])
    bars = asof.day_bars(df, day)
    for i in (0, 10, len(bars) - 1):
        assert len(asof.session_upto(df, day, bars.index[i])) == i + 1


def test_day_bars_isolates_one_et_date():
    df = _intraday()
    got = asof.day_bars(df, pd.Timestamp(_DAYS[1]))
    assert set(asof.naive_dates(got.index)) == {pd.Timestamp(_DAYS[1])}


def test_asof_daily_partial_bar_is_the_running_session_not_the_full_day():
    """The synthesized 'today' row must be built from the session *so far* — this
    is what makes the Bollinger / overextension guards see the gap the way live
    does, instead of a completed bar the radar could not have known."""
    df = _intraday()
    day = pd.Timestamp(_DAYS[1])
    sess = asof.day_bars(df, day)
    prior = _daily()

    partial = asof.asof_daily(prior, sess.iloc[:10], day)
    row = partial.loc[day]
    assert row["open"] == sess["open"].iloc[0]
    assert row["high"] == sess["high"].iloc[:10].max()
    assert row["low"] == sess["low"].iloc[:10].min()
    assert row["close"] == sess["close"].iloc[9]
    assert row["volume"] == sess["volume"].iloc[:10].sum()
    # ...and strictly *not* the completed day.
    assert row["high"] < sess["high"].max()
    assert len(partial) == len(prior) + 1


def test_asof_daily_with_no_session_bars_returns_prior_only():
    prior = _daily()
    out = asof.asof_daily(prior, prior.iloc[:0], pd.Timestamp(_DAYS[1]))
    assert out is prior


def test_trading_days_lists_every_session():
    assert asof.trading_days(_intraday()) == [pd.Timestamp(d) for d in _DAYS]


def test_asof_reports_omits_frames_under_the_minimum_bar_count():
    """A timeframe with too little history is dropped, not analyzed on noise."""
    i5, i30 = _intraday(), _intraday("30min", per_day=13)
    daily = {Timeframe.M1: _daily()}
    day = pd.Timestamp(_DAYS[1])
    bars = asof.day_bars(i5, day)

    early = asof.asof_reports(i5, i30, daily, day, bars.index[3])
    assert Timeframe.D1 in early                      # 78 prior-day bars + 4 today
    assert Timeframe.M1 in early                      # 60 completed dailies + partial

    thin = asof.asof_reports(i5.iloc[-5:], i30.iloc[-5:], {}, day, bars.index[3])
    assert thin == {}


def test_asof_reports_daily_frame_carries_the_partial_bar():
    i5, i30 = _intraday(), _intraday("30min", per_day=13)
    day = pd.Timestamp(_DAYS[1])
    t = asof.day_bars(i5, day).index[20]
    reports = asof.asof_reports(i5, i30, {Timeframe.M1: _daily()}, day, t)
    m1 = reports[Timeframe.M1].df
    assert m1.index[-1] == day
    assert m1.loc[day, "close"] == asof.session_upto(i5, day, t)["close"].iloc[-1]


def test_decision_rep_prefers_d5_then_d1_then_m1():
    a, b, c = object(), object(), object()
    assert asof.decision_rep({Timeframe.D5: a, Timeframe.D1: b, Timeframe.M1: c}) is a
    assert asof.decision_rep({Timeframe.D1: b, Timeframe.M1: c}) is b
    assert asof.decision_rep({Timeframe.M1: c}) is c
    assert asof.decision_rep({}) is None


def test_daytrade_backtest_shares_the_asof_module():
    """The CLI must *import* the as-of logic, never keep its own copy.

    Two consumers silently drifting apart is the failure this pins: a backtest
    that no longer reconstructs what the live radar saw is hindsight wearing a
    backtest's clothes. See algolab/LEARNINGS.md.
    """
    import daytrade_backtest as cli

    for private, public in (("_naive_dates", "naive_dates"),
                            ("_trading_days", "trading_days"),
                            ("_asof_intraday", "asof_intraday"),
                            ("_day_bars", "day_bars"),
                            ("_session_upto", "session_upto"),
                            ("_asof_daily", "asof_daily"),
                            ("_asof_reports", "asof_reports"),
                            ("_decision_rep", "decision_rep"),
                            ("_higher_trend", "higher_trend")):
        assert getattr(cli, private) is getattr(asof, public), private
