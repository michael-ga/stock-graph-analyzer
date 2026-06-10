"""Indicator math computed directly in pandas/numpy.

Done by hand (rather than via pandas-ta / TA-Lib) for three reasons: no fragile
native/Windows build, no numpy-version breakage, and full transparency so the
verdict can explain exactly what each number means.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100.0).where(avg_loss != 0, 100.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3
) -> pd.DataFrame:
    lowest = low.rolling(k, min_periods=k).min()
    highest = high.rolling(k, min_periods=k).max()
    percent_k = 100 * (close - lowest) / (highest - lowest).replace(0.0, np.nan)
    percent_d = percent_k.rolling(d, min_periods=d).mean()
    return pd.DataFrame({"k": percent_k, "d": percent_d})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(close, length)
    std = close.rolling(length, min_periods=length).std()
    return pd.DataFrame({"mid": mid, "upper": mid + mult * std, "lower": mid - mult * std})


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the standard indicator set used across the engine and charts."""
    out = df.copy()
    out["sma20"] = sma(df["close"], 20)
    out["sma50"] = sma(df["close"], 50)
    out["sma200"] = sma(df["close"], 200)
    out["ema20"] = ema(df["close"], 20)
    out["rsi"] = rsi(df["close"], 14)
    m = macd(df["close"])
    out[["macd", "macd_signal", "macd_hist"]] = m[["macd", "signal", "hist"]]
    s = stochastic(df["high"], df["low"], df["close"])
    out[["stoch_k", "stoch_d"]] = s[["k", "d"]]
    out["atr"] = atr(df["high"], df["low"], df["close"], 14)
    return out
