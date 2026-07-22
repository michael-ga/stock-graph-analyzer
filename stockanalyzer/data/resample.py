"""Aggregate an OHLCV frame to a coarser candle interval.

Candle size is a *view* concern, not a fetch concern. The 1D frame already
arrives as 5-minute bars, and a 15m or 30m view is an exact integer roll-up of
those bars — so deriving it locally costs no network round trip and burns no
rate-limit budget. That also keeps the aggregation a pure function, testable
without touching a provider.

Only intervals that are exact multiples of the frame's native bar are offered:
rolling 5m into 15m is lossless, but "deriving" 5m from 30m is not a thing, and
a non-integer multiple (e.g. 45m from 30m bars) would silently mis-bin.
"""
from __future__ import annotations

import pandas as pd

from .schema import OHLCV_COLUMNS

_AGG = {"open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum"}

# Candle sizes the chart offers. Each divides evenly into the 09:30 ET session
# open when measured from midnight (570 min ÷ 5, 15, 30 are all whole numbers),
# so bins land on the open rather than straddling it. 60m is deliberately absent:
# 570/60 = 9.5, so midnight-origin hourly bins would cut the first hour in half.
CHART_INTERVALS_MIN = (5, 15, 30)


def infer_interval_minutes(df: pd.DataFrame | None) -> float | None:
    """Median spacing between bars, in minutes; None if undeterminable.

    Median (not mean) because intraday frames contain overnight gaps that would
    drag an average far past the true bar width.
    """
    if df is None or len(df) < 3 or not isinstance(df.index, pd.DatetimeIndex):
        return None
    deltas = pd.Series(df.index).diff().dropna()
    if deltas.empty:
        return None
    minutes = deltas.median().total_seconds() / 60.0
    return minutes if minutes > 0 else None


def available_intervals(df: pd.DataFrame | None) -> list[int]:
    """Chart intervals that are exact integer roll-ups of this frame's bars.

    The frame's own bar width always leads the list, so ``result[0]`` is the
    native interval and anything after it needs a roll-up. Returns [] for
    daily/weekly frames (base ≥ a full session) or when the bar width can't be
    inferred — the caller then shows no interval picker.
    """
    base = infer_interval_minutes(df)
    if base is None:
        return []
    b = int(round(base))
    if b <= 0 or b > max(CHART_INTERVALS_MIN):
        return []
    return sorted({b} | {m for m in CHART_INTERVALS_MIN if m > b and m % b == 0})


def resample_ohlcv(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Roll `df` up to `minutes`-wide candles, returning raw OHLCV only.

    Indicator columns are deliberately dropped rather than carried over: RSI on
    5-minute bars is a different series from RSI on 15-minute bars, so the caller
    must re-run the engine on the result instead of inheriting stale columns.
    """
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("resample_ohlcv requires a DatetimeIndex")

    # left/left: a bar stamped 09:30 covers 09:30–09:45, matching how the
    # providers label their intraday bars.
    out = (df[OHLCV_COLUMNS]
           .resample(f"{minutes}min", label="left", closed="left")
           .agg(_AGG))
    # resample materializes a bin for every gap (overnight, weekends, halts);
    # those come back all-NaN and must not become candles.
    return out.dropna(subset=["open", "high", "low", "close"])
