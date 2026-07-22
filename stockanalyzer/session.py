"""Wall-clock US market-session helpers (Eastern Time).

Distinct from ``data.market_session`` (which classifies *bar* timestamps): these
answer "what is the market doing right NOW" for the live engine — the Gap-and-Go
opening-range freeze (first 15 min), the morning entry window (9:45–11:30), and
the Friday flatten cutoff.

Timezone conversion goes through pandas so it works on Windows without the IANA
``tzdata`` package (pandas carries its own zone data). A tz-naive input is
treated as already-Eastern, matching the ``data.market_session`` convention.
"""
from __future__ import annotations

from datetime import time

import pandas as pd

_ET = "America/New_York"

_OPEN      = time(9, 30)     # regular session open
_OR_END    = time(9, 45)     # opening range = first 15 minutes (9:30–9:45)
_ORB_START = time(9, 45)     # Gap-and-Go entry window opens
_ORB_END   = time(11, 30)    # ...and closes (no Gap-and-Go entries after this)
_PRE_START = time(4, 0)
_REG_END   = time(16, 0)
_FRI_FLAT  = time(15, 45)    # flatten day-trades into the Friday close


def now_et() -> pd.Timestamp:
    """Current wall-clock time in US/Eastern."""
    return pd.Timestamp.now(tz=_ET)


def _et(now=None) -> pd.Timestamp:
    """Coerce an input to an Eastern-time Timestamp (naive == already-Eastern)."""
    if now is None:
        return now_et()
    ts = pd.Timestamp(now)
    if ts.tzinfo is None:
        return ts                       # assume already Eastern
    try:
        return ts.tz_convert(_ET)
    except Exception:
        return ts.tz_localize(None)


def _is_weekday(ts: pd.Timestamp) -> bool:
    return ts.weekday() < 5             # Mon..Fri


def market_phase(now=None) -> str:
    """One of: premarket | opening_range | regular | closed.

    ``opening_range`` is the first 15 minutes after the open — watched, but the
    Gap-and-Go engine takes no entries during it.
    """
    ts = _et(now)
    if not _is_weekday(ts):
        return "closed"
    t = ts.time()
    if _PRE_START <= t < _OPEN:
        return "premarket"
    if _OPEN <= t < _OR_END:
        return "opening_range"
    if _OR_END <= t < _REG_END:
        return "regular"
    return "closed"


def minutes_since_open(now=None) -> float:
    """Minutes since 9:30 ET today (negative before the open)."""
    ts = _et(now)
    open_ts = ts.normalize() + pd.Timedelta(hours=9, minutes=30)
    return (ts - open_ts).total_seconds() / 60.0


def is_orb_window(now=None) -> bool:
    """True inside the Gap-and-Go entry window (9:45–11:30 ET, weekdays)."""
    ts = _et(now)
    return _is_weekday(ts) and _ORB_START <= ts.time() <= _ORB_END


def is_friday_flat(now=None) -> bool:
    """True in the last 15 min of a Friday regular session (flatten cutoff)."""
    ts = _et(now)
    return ts.weekday() == 4 and _FRI_FLAT <= ts.time() < _REG_END
