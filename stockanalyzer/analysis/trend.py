"""Trend identification via Dow Theory: swing highs/lows and the sequence of
higher-highs/higher-lows (uptrend) vs lower-highs/lower-lows (downtrend),
confirmed by moving-average slope and price position.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from .indicators import sma
from .signals import Direction, Signal


@dataclass
class Swing:
    idx: int          # positional index into the frame
    price: float
    kind: str         # "high" or "low"


def _collapse(idx: np.ndarray, values: np.ndarray, order: int, take_max: bool) -> list[int]:
    """Merge runs of adjacent extrema (flat plateaus produced by *_equal) into a
    single representative index — the highest high / lowest low of each run.
    """
    if len(idx) == 0:
        return []
    runs: list[list[int]] = [[int(idx[0])]]
    for i in idx[1:]:
        if int(i) - runs[-1][-1] <= order:
            runs[-1].append(int(i))
        else:
            runs.append([int(i)])
    out = []
    for run in runs:
        best = max(run, key=lambda j: values[j]) if take_max else min(run, key=lambda j: values[j])
        out.append(best)
    return out


def find_swings(df: pd.DataFrame, order: int = 3) -> list[Swing]:
    """Local maxima of high and minima of low. ``order`` = bars on each side that
    must be lower/higher (Murphy's "peaks and troughs"). Returned in time order.
    """
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    order = max(1, min(order, (n - 1) // 2)) if n > 2 else 1

    hi_idx = argrelextrema(highs, np.greater_equal, order=order)[0]
    lo_idx = argrelextrema(lows, np.less_equal, order=order)[0]

    hi_idx = _collapse(hi_idx, highs, order, take_max=True)
    lo_idx = _collapse(lo_idx, lows, order, take_max=False)

    swings = [Swing(int(i), float(highs[i]), "high") for i in hi_idx]
    swings += [Swing(int(i), float(lows[i]), "low") for i in lo_idx]
    swings.sort(key=lambda s: s.idx)
    return swings


def detect_trend(df: pd.DataFrame, order: int = 3) -> Signal:
    """Classify the prevailing trend and return a Signal with evidence."""
    swings = find_swings(df, order=order)
    highs = [s for s in swings if s.kind == "high"][-3:]
    lows = [s for s in swings if s.kind == "low"][-3:]

    higher_highs = len(highs) >= 2 and all(
        highs[i].price > highs[i - 1].price for i in range(1, len(highs))
    )
    higher_lows = len(lows) >= 2 and all(
        lows[i].price > lows[i - 1].price for i in range(1, len(lows))
    )
    lower_highs = len(highs) >= 2 and all(
        highs[i].price < highs[i - 1].price for i in range(1, len(highs))
    )
    lower_lows = len(lows) >= 2 and all(
        lows[i].price < lows[i - 1].price for i in range(1, len(lows))
    )

    close = df["close"]
    ma = sma(close, min(50, max(2, len(df) // 2)))
    ma_slope_up = ma.notna().sum() >= 2 and ma.iloc[-1] > ma.dropna().iloc[0]
    price_above_ma = bool(ma.notna().any() and close.iloc[-1] > ma.dropna().iloc[-1])

    if higher_highs and higher_lows:
        direction, base, struct = Direction.BULL, 0.7, "higher highs and higher lows"
    elif lower_highs and lower_lows:
        direction, base, struct = Direction.BEAR, 0.7, "lower highs and lower lows"
    elif higher_lows or (price_above_ma and ma_slope_up):
        direction, base, struct = Direction.BULL, 0.4, "rising lows / price above rising MA"
    elif lower_highs or (not price_above_ma and not ma_slope_up):
        direction, base, struct = Direction.BEAR, 0.4, "falling highs / price below falling MA"
    else:
        direction, base, struct = Direction.NEUTRAL, 0.3, "no clear higher/lower swing sequence (sideways)"

    # MA agreement strengthens the read.
    if direction == Direction.BULL and price_above_ma and ma_slope_up:
        base = min(1.0, base + 0.2)
    if direction == Direction.BEAR and not price_above_ma and not ma_slope_up:
        base = min(1.0, base + 0.2)

    return Signal(
        name="trend",
        direction=direction,
        strength=base,
        evidence=f"Dow-Theory structure: {struct}.",
        category="trend",
        meta={"price_above_ma": price_above_ma, "ma_slope_up": bool(ma_slope_up)},
    )
