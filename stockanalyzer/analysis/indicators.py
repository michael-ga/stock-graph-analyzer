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


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        length: int = 14) -> pd.DataFrame:
    """Wilder's ADX / DMI — the canonical trend-strength + direction gauge.

    `adx` measures how *strongly* price trends (not the direction): >25 trending,
    <20 chop. `plus_di`/`minus_di` give the direction — +DI over −DI = buyers in
    control. Together they answer the question the radar kept getting wrong: is
    this a real trend to ride, a downtrend to avoid going long into, or chop to
    skip? Uses Wilder's smoothing (the RSI/ATR convention already in this file).
    """
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0.0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False,
                                min_periods=length).mean() / atr_w.replace(0.0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False,
                                  min_periods=length).mean() / atr_w.replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_line = dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    return pd.DataFrame({"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di})


def relative_volume(df: pd.DataFrame, length: int = 20) -> float | None:
    """Current bar's volume ÷ trailing average volume (RVOL).

    The breakout-confirmation gauge: RVOL > 1 means the latest bar traded above
    its recent norm — the volume surge that separates a real break from a drift.
    Returns ``None`` when volume is missing or there's too little history to judge.
    """
    if df is None or "volume" not in df or len(df) < length:
        return None
    vol = df["volume"].dropna()
    if len(vol) < length:
        return None
    avg = float(vol.tail(length).mean())
    if avg <= 0:
        return None
    return float(vol.iloc[-1] / avg)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the standard indicator set used across the engine and charts."""
    out = df.copy()
    out["sma20"] = sma(df["close"], 20)
    out["sma50"] = sma(df["close"], 50)
    out["sma200"] = sma(df["close"], 200)
    out["ema8"] = ema(df["close"], 8)                 # fast intraday trail reference
    out["ema20"] = ema(df["close"], 20)
    out["rsi"] = rsi(df["close"], 14)
    m = macd(df["close"])
    out[["macd", "macd_signal", "macd_hist"]] = m[["macd", "signal", "hist"]]
    s = stochastic(df["high"], df["low"], df["close"])
    out[["stoch_k", "stoch_d"]] = s[["k", "d"]]
    out["atr"] = atr(df["high"], df["low"], df["close"], 14)
    bb = bollinger(df["close"])                       # overextension / blowoff guard
    out[["bb_mid", "bb_upper", "bb_lower"]] = bb[["mid", "upper", "lower"]]
    dmi = adx(df["high"], df["low"], df["close"])      # trend strength + direction
    out[["adx", "plus_di", "minus_di"]] = dmi[["adx", "plus_di", "minus_di"]]
    return out
