"""Normalized OHLCV contract shared by every data source and the analysis engine.

The engine never knows where candles came from (API or image). It only relies on
a DataFrame with a DatetimeIndex and the columns in ``OHLCV_COLUMNS``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class Timeframe(str, Enum):
    """The ranges the app analyzes. Declaration order = dashboard tab order."""

    D1 = "1D"
    D5 = "5D"
    M1 = "1M"
    M6 = "6M"
    YTD = "YTD"
    Y1 = "1Y"
    Y5 = "5Y"


@dataclass(frozen=True)
class TimeframeSpec:
    """How to request a given range from a provider.

    period:   how far back to fetch (yfinance-style string)
    interval: candle granularity (yfinance-style string)
    td_interval/td_outputsize: Twelve Data equivalents
    """

    period: str
    interval: str
    td_interval: str
    td_outputsize: int


# Granularity chosen so each range shows a readable number of candles, the way a
# trading site does: intraday for short ranges, daily/weekly for long ranges.
TIMEFRAME_SPECS: dict[Timeframe, TimeframeSpec] = {
    # "1D" uses a 2-day window so that early in the day (e.g. pre-market) the chart
    # still shows the prior full session + today's pre-market — matching how trading
    # sites render their "1D" view, and giving the pre-market gap a reference close.
    Timeframe.D1: TimeframeSpec("2d", "5m", "5min", 160),
    Timeframe.D5: TimeframeSpec("5d", "30m", "30min", 65),
    Timeframe.M1: TimeframeSpec("1mo", "1d", "1day", 22),
    Timeframe.M6: TimeframeSpec("6mo", "1d", "1day", 126),
    # YTD: daily bars; Twelve Data outputsize is recomputed at fetch time.
    Timeframe.YTD: TimeframeSpec("ytd", "1d", "1day", 252),
    Timeframe.Y1: TimeframeSpec("1y", "1d", "1day", 252),
    # 5Y: weekly bars keep the candle count readable.
    Timeframe.Y5: TimeframeSpec("5y", "1wk", "1week", 260),
}


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce/verify a DataFrame into the canonical OHLCV contract.

    Guarantees: DatetimeIndex (sorted, ascending), float OHLC, numeric volume,
    lowercase columns, no all-NaN price rows. Raises ValueError otherwise.
    """
    if df is None or len(df) == 0:
        raise ValueError("empty OHLCV frame")

    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=False, errors="coerce")
    df = df[~df.index.isna()]
    df = df.sort_index()

    for col in OHLCV_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0.0)

    if len(df) == 0:
        raise ValueError("no valid rows after cleaning OHLCV frame")

    return df[OHLCV_COLUMNS]
