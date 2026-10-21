"""Classify intraday timestamps into US market sessions.

Windows (Eastern Time):
  pre-market   04:00–09:30
  regular      09:30–16:00
  after-hours  16:00–20:00
  closed       otherwise

US hours are assumed; non-US tickers are approximate. A tz-naive timestamp is
treated as already-Eastern; a tz-aware one is converted to America/New_York.
"""
from __future__ import annotations

from datetime import time
from enum import Enum

import pandas as pd

_ET = "America/New_York"
_PRE_START = time(4, 0)
_REG_START = time(9, 30)
_REG_END = time(16, 0)
_POST_END = time(20, 0)


class Session(str, Enum):
    PRE = "pre-market"
    REGULAR = "regular"
    POST = "after-hours"
    CLOSED = "closed"

    @property
    def is_extended(self) -> bool:
        return self in (Session.PRE, Session.POST)


def _to_et(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts                       # assume already Eastern
    try:
        return ts.tz_convert(_ET)
    except Exception:
        return ts.tz_localize(None)


def classify(ts) -> Session:
    t = _to_et(ts).time()
    if _PRE_START <= t < _REG_START:
        return Session.PRE
    if _REG_START <= t < _REG_END:
        return Session.REGULAR
    if _REG_END <= t < _POST_END:
        return Session.POST
    return Session.CLOSED


def sessions_for_index(index: pd.DatetimeIndex) -> list[Session]:
    return [classify(ts) for ts in index]


def is_intraday(df: pd.DataFrame) -> bool:
    """True if the frame has more than one distinct time-of-day (i.e. intraday bars)."""
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 2:
        return False
    times = {pd.Timestamp(ts).strftime("%H:%M") for ts in df.index[:50]}
    return len(times) > 1


def last_regular_close(df: pd.DataFrame) -> float | None:
    """Close of the most recent REGULAR-session bar, used as the gap reference."""
    for ts in reversed(df.index):
        if classify(ts) == Session.REGULAR:
            return float(df.loc[ts, "close"])
    return None


def regular_open(df: pd.DataFrame) -> float | None:
    """Open of the first REGULAR-session bar (09:30 ET) of the latest ET date in
    ``df`` — the gap reference. With ``prepost=True`` data the latest day starts
    at 04:00, so ``df["open"].iloc[0]`` is the *pre-market* open, which mis-states
    the gap. ``None`` until the 09:30 bar exists (or for daily bars)."""
    if not isinstance(df.index, pd.DatetimeIndex) or df.empty:
        return None
    last_day = _to_et(df.index[-1]).date()
    for ts in df.index:
        et = _to_et(ts)
        if et.date() == last_day and classify(et) == Session.REGULAR:
            return float(df.loc[ts, "open"])
    return None


def session_closes(df: pd.DataFrame, n: int = 2) -> list[tuple]:
    """The last ``n`` distinct trading days' REGULAR-session closes in ``df``,
    most recent first, as ``(date, close)`` pairs.

    Exists to recover an accurate day-over-day change when a daily-bar feed is
    missing its most recent row — a real provider quirk where the daily candle
    for a session that already closed hasn't backfilled yet. The intraday frame
    (fetched at finer resolution) already has that session's bars, so callers
    can cross-check the daily series against this and prefer it when the daily
    frame turns out to be a session behind.
    """
    if not isinstance(df.index, pd.DatetimeIndex) or df.empty:
        return []
    out: list[tuple] = []
    seen: set = set()
    for ts in reversed(df.index):
        if classify(ts) != Session.REGULAR:
            continue
        day = _to_et(ts).date()
        if day in seen:
            continue
        seen.add(day)
        out.append((day, float(df.loc[ts, "close"])))
        if len(out) >= n:
            break
    return out
