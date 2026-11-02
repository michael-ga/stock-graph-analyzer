"""Point-in-time ("as of") frame reconstruction — the no-lookahead primitives.

Rebuilding *exactly what the live radar would have seen at bar t* is the whole
basis of any honest backtest or replay. Getting it subtly wrong (an extra bar, a
completed daily bar where live only had a partial one) silently turns a backtest
into hindsight, and the result looks plausible.

So this lives in one module and every consumer imports it: the intraday backtest
CLI (``daytrade_backtest.py``) and the interactive replay simulator
(``stockanalyzer/replay.py``). See ``algolab/LEARNINGS.md`` — one pure as-of
function shared by every consumer, no drift.

Fidelity notes (parity with the live radar):
- The live 1D frame is only the last ~2 days of 5-min bars, so each as-of slice is
  trailing-2-days up to the bar (`asof_intraday`).
- The swing guards (`overext`/`leg_spent`, "no chase", ADX, Bollinger) read the
  DAILY frame, whose last bar live is *today's partial bar*. We rebuild that partial
  bar from the session so far (`asof_daily`) — this is what makes a +8% gap trip the
  2σ Bollinger veto, so the diagnosis is faithful.
"""
from __future__ import annotations

import pandas as pd

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.data.schema import Timeframe

ET = "America/New_York"
REG_START, OR_END, REG_END = "09:30", "09:45", "16:00"

# A timeframe needs this many bars before its indicators mean anything.
MIN_BARS = 20


def naive_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """ET wall-clock dates as a tz-naive index (so it compares to the daily frames)."""
    idx = index.tz_localize(None) if index.tz is not None else index
    return idx.normalize()


def trading_days(intraday5: pd.DataFrame) -> list[pd.Timestamp]:
    """Distinct ET dates that actually opened a regular session (have a 09:30 bar)."""
    reg = intraday5.between_time(REG_START, REG_END, inclusive="left")
    return sorted(set(naive_dates(reg.index)))


def asof_intraday(intraday: pd.DataFrame, t: pd.Timestamp, days: int) -> pd.DataFrame:
    """Bars up to and including t, trailing `days` calendar days (mirrors the live
    fixed-period intraday window)."""
    lo = t - pd.Timedelta(days=days)
    return intraday[(intraday.index > lo) & (intraday.index <= t)]


def day_bars(intraday: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """All bars whose ET date == day (day is a tz-naive normalized date)."""
    return intraday[naive_dates(intraday.index) == day]


def session_upto(intraday5: pd.DataFrame, day: pd.Timestamp,
                 t: pd.Timestamp) -> pd.DataFrame:
    """Regular-session bars of `day` from the open through t."""
    d = day_bars(intraday5, day).between_time(REG_START, REG_END, inclusive="left")
    return d[d.index <= t]


def asof_daily(daily_prior: pd.DataFrame, session_bars: pd.DataFrame,
               day: pd.Timestamp) -> pd.DataFrame:
    """Daily frame = completed prior days + today's PARTIAL bar built from the
    session so far. This is what the live daily frame looks like intraday, so the
    Bollinger/overextension/chase guards see the gap (the whole point)."""
    if session_bars.empty:
        return daily_prior
    today = pd.DataFrame({
        "open": [float(session_bars["open"].iloc[0])],
        "high": [float(session_bars["high"].max())],
        "low": [float(session_bars["low"].min())],
        "close": [float(session_bars["close"].iloc[-1])],
        "volume": [float(session_bars["volume"].sum())],
    }, index=[day])
    return pd.concat([daily_prior, today])


def asof_reports(intraday5, intraday30, daily, day, t) -> dict:
    """The full multi-timeframe view the radar would have had at bar t."""
    sess = session_upto(intraday5, day, t)
    reports: dict = {}
    d1 = asof_intraday(intraday5, t, days=2)
    if len(d1) >= MIN_BARS:
        reports[Timeframe.D1] = analyze_timeframe(d1)
    d5 = asof_intraday(intraday30, t, days=5)
    if len(d5) >= MIN_BARS:
        reports[Timeframe.D5] = analyze_timeframe(d5)
    for tf in (Timeframe.M1, Timeframe.M6, Timeframe.Y1):
        base = daily.get(tf)
        if base is None:
            continue
        prior = base[naive_dates(base.index) < day]
        frame = asof_daily(prior, sess, day)
        if len(frame) >= MIN_BARS:
            reports[tf] = analyze_timeframe(frame)
    return reports


def decision_rep(reports: dict):
    """Same preference the radar uses (D5 → D1 → M1)."""
    for tf in (Timeframe.D5, Timeframe.D1, Timeframe.M1):
        if reports.get(tf) is not None:
            return reports[tf]
    return None


def higher_trend(reports: dict):
    """The prevailing (daily) trend direction the reversal engine gates on."""
    for tf in (Timeframe.M1, Timeframe.D5, Timeframe.M6):
        rep = reports.get(tf)
        if rep is not None:
            return rep.trend.direction
    return None
