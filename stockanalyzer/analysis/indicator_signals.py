"""Turn the raw indicator series into Signals: MA crosses, RSI levels & divergence,
MACD crosses, stochastic crosses in OB/OS zones.
"""
from __future__ import annotations

import pandas as pd

from .signals import Direction, Signal
from .trend import find_swings


def _last_cross(fast: pd.Series, slow: pd.Series) -> int:
    """+1 if fast crossed above slow on the last bar, -1 if below, 0 otherwise."""
    f, s = fast.dropna().align(slow.dropna(), join="inner")
    if len(f) < 2:
        return 0
    prev, now = f.iloc[-2] - s.iloc[-2], f.iloc[-1] - s.iloc[-1]
    if prev <= 0 < now:
        return 1
    if prev >= 0 > now:
        return -1
    return 0


def ma_signals(df: pd.DataFrame) -> list[Signal]:
    out: list[Signal] = []
    if "sma50" in df and "sma200" in df and df[["sma50", "sma200"]].dropna().shape[0] >= 2:
        cross = _last_cross(df["sma50"], df["sma200"])
        if cross > 0:
            out.append(Signal("golden_cross", Direction.BULL, 0.8,
                              "50-MA crossed above 200-MA (golden cross).", category="trend"))
        elif cross < 0:
            out.append(Signal("death_cross", Direction.BEAR, 0.8,
                              "50-MA crossed below 200-MA (death cross).", category="trend"))
    return out


def rsi_signals(df: pd.DataFrame) -> list[Signal]:
    out: list[Signal] = []
    if "rsi" not in df or df["rsi"].dropna().empty:
        return out
    r = df["rsi"]
    last = float(r.iloc[-1])
    if last >= 70:
        out.append(Signal("rsi_overbought", Direction.BEAR, 0.5,
                          f"RSI {last:.0f} — overbought.", category="momentum"))
    elif last <= 30:
        out.append(Signal("rsi_oversold", Direction.BULL, 0.5,
                          f"RSI {last:.0f} — oversold.", category="momentum"))

    div = _rsi_divergence(df)
    if div:
        out.append(div)
    return out


def _rsi_divergence(df: pd.DataFrame) -> Signal | None:
    """Compare the last two price swing highs/lows with RSI at those points."""
    swings = find_swings(df, order=3)
    r = df["rsi"]
    highs = [s for s in swings if s.kind == "high"][-2:]
    lows = [s for s in swings if s.kind == "low"][-2:]

    if len(highs) == 2:
        p0, p1 = highs[0], highs[1]
        if p1.price > p0.price and r.iloc[p1.idx] < r.iloc[p0.idx]:
            return Signal("rsi_bearish_divergence", Direction.BEAR, 0.65,
                          "Price made a higher high but RSI made a lower high "
                          "(bearish divergence) — possible reversal.", category="momentum")
    if len(lows) == 2:
        p0, p1 = lows[0], lows[1]
        if p1.price < p0.price and r.iloc[p1.idx] > r.iloc[p0.idx]:
            return Signal("rsi_bullish_divergence", Direction.BULL, 0.65,
                          "Price made a lower low but RSI made a higher low "
                          "(bullish divergence) — possible reversal.", category="momentum")
    return None


def macd_signals(df: pd.DataFrame) -> list[Signal]:
    out: list[Signal] = []
    if "macd" not in df or "macd_signal" not in df:
        return out
    cross = _last_cross(df["macd"], df["macd_signal"])
    if cross > 0:
        out.append(Signal("macd_bull_cross", Direction.BULL, 0.55,
                          "MACD crossed above its signal line.", category="momentum"))
    elif cross < 0:
        out.append(Signal("macd_bear_cross", Direction.BEAR, 0.55,
                          "MACD crossed below its signal line.", category="momentum"))
    return out


def stochastic_signals(df: pd.DataFrame) -> list[Signal]:
    out: list[Signal] = []
    if "stoch_k" not in df or df["stoch_k"].dropna().empty:
        return out
    k = float(df["stoch_k"].iloc[-1])
    cross = _last_cross(df["stoch_k"], df["stoch_d"])
    if k <= 20 and cross > 0:
        out.append(Signal("stoch_bull", Direction.BULL, 0.45,
                          f"Stochastic %K {k:.0f} crossing up from oversold.", category="momentum"))
    elif k >= 80 and cross < 0:
        out.append(Signal("stoch_bear", Direction.BEAR, 0.45,
                          f"Stochastic %K {k:.0f} crossing down from overbought.", category="momentum"))
    return out
