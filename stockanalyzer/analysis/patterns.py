"""Chart patterns from swing-point geometry: double top/bottom and
head & shoulders / inverse head & shoulders. Neckline break + (optional) volume
confirmation, per Murphy.
"""
from __future__ import annotations

import pandas as pd

from .indicators import atr
from .signals import Direction, Signal
from .trend import Swing, find_swings
from .volume import confirms_breakout


def _tol(df: pd.DataFrame) -> float:
    a = atr(df["high"], df["low"], df["close"], min(14, max(2, len(df) // 2)))
    last = df["close"].iloc[-1]
    return float(a.dropna().iloc[-1]) if a.notna().any() else last * 0.02


def pattern_signals(df: pd.DataFrame, order: int = 3) -> list[Signal]:
    swings = find_swings(df, order=order)
    out: list[Signal] = []
    out += _double(df, swings)
    out += _head_shoulders(df, swings)
    return out


def _double(df: pd.DataFrame, swings: list[Swing]) -> list[Signal]:
    out: list[Signal] = []
    tol = _tol(df)
    last_close = df["close"].iloc[-1]
    vol_ok = confirms_breakout(df)

    highs = [s for s in swings if s.kind == "high"][-2:]
    lows = [s for s in swings if s.kind == "low"][-2:]

    # Double top: two similar highs with a trough between; confirmed on break below trough.
    if len(highs) == 2 and abs(highs[0].price - highs[1].price) <= tol:
        between = [s for s in swings if s.kind == "low" and highs[0].idx < s.idx < highs[1].idx]
        if between:
            neckline = min(s.price for s in between)
            if last_close < neckline:
                out.append(Signal("double_top", Direction.BEAR, 0.75 if vol_ok else 0.6,
                                  f"Double top near {highs[1].price:.2f}; close broke neckline "
                                  f"{neckline:.2f}{' on volume' if vol_ok else ''} — bearish reversal.",
                                  category="pattern"))

    # Double bottom: two similar lows with a peak between; confirmed on break above peak.
    if len(lows) == 2 and abs(lows[0].price - lows[1].price) <= tol:
        between = [s for s in swings if s.kind == "high" and lows[0].idx < s.idx < lows[1].idx]
        if between:
            neckline = max(s.price for s in between)
            if last_close > neckline:
                out.append(Signal("double_bottom", Direction.BULL, 0.75 if vol_ok else 0.6,
                                  f"Double bottom near {lows[1].price:.2f}; close broke neckline "
                                  f"{neckline:.2f}{' on volume' if vol_ok else ''} — bullish reversal.",
                                  category="pattern"))
    return out


def _head_shoulders(df: pd.DataFrame, swings: list[Swing]) -> list[Signal]:
    out: list[Signal] = []
    tol = _tol(df)
    last_close = df["close"].iloc[-1]

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    # Head & shoulders top: 3 highs, middle highest, shoulders ~equal.
    if len(highs) >= 3:
        l, h, r = highs[-3], highs[-2], highs[-1]
        if h.price > l.price and h.price > r.price and abs(l.price - r.price) <= 2 * tol:
            necks = [s for s in lows if l.idx < s.idx < r.idx]
            if necks:
                neckline = min(s.price for s in necks)
                if last_close < neckline:
                    out.append(Signal("head_and_shoulders", Direction.BEAR, 0.8,
                                      f"Head & shoulders top; close broke neckline {neckline:.2f} "
                                      "— bearish reversal.", category="pattern"))

    # Inverse head & shoulders: 3 lows, middle lowest, shoulders ~equal.
    if len(lows) >= 3:
        l, h, r = lows[-3], lows[-2], lows[-1]
        if h.price < l.price and h.price < r.price and abs(l.price - r.price) <= 2 * tol:
            necks = [s for s in highs if l.idx < s.idx < r.idx]
            if necks:
                neckline = max(s.price for s in necks)
                if last_close > neckline:
                    out.append(Signal("inverse_head_and_shoulders", Direction.BULL, 0.8,
                                      f"Inverse head & shoulders; close broke neckline {neckline:.2f} "
                                      "— bullish reversal.", category="pattern"))
    return out
