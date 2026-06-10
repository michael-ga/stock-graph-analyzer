"""Extended-hours (pre-market / after-hours) move detector.

When the latest intraday bar is in an extended session, compare it to the prior
regular-session close. A meaningful gap is an early heads-up that the next open
may shift — the kind of move other charting sites show but a regular-hours-only
feed misses.
"""
from __future__ import annotations

import pandas as pd

from ..data.market_session import Session, classify, is_intraday, last_regular_close
from .signals import Direction, Signal

_MIN_GAP = 0.004   # 0.4% — ignore noise


def extended_session_signal(df: pd.DataFrame) -> Signal | None:
    if not is_intraday(df) or len(df) < 2:
        return None

    sess = classify(df.index[-1])
    if not sess.is_extended:
        return None

    ref = last_regular_close(df)
    if not ref:
        return None

    last_price = float(df["close"].iloc[-1])
    gap = (last_price - ref) / ref
    if abs(gap) < _MIN_GAP:
        return None

    up = gap > 0
    direction = Direction.BULL if up else Direction.BEAR
    strength = min(1.0, 0.4 + abs(gap) * 10)

    if sess == Session.PRE:
        name = "premarket_gap_up" if up else "premarket_gap_down"
        when = "Pre-market"
    else:
        name = "afterhours_move_up" if up else "afterhours_move_down"
        when = "After-hours"

    return Signal(
        name=name,
        direction=direction,
        strength=strength,
        evidence=(f"{when} is {gap:+.1%} vs the prior close (${ref:.2f} → ${last_price:.2f}) "
                  f"— a move that may shift the next open."),
        category="session",
        meta={"gap_pct": round(gap * 100, 2), "session": sess.value, "ref_close": ref},
    )
