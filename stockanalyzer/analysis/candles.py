"""Candlestick patterns from explicit OHLC ratio rules (last bar, with context).
Covers doji, hammer, shooting star, bullish/bearish engulfing, harami.
"""
from __future__ import annotations

import pandas as pd

from .signals import Direction, Signal


def _parts(o, h, l, c):
    body = abs(c - o)
    rng = h - l if h > l else 1e-9
    upper = h - max(o, c)
    lower = min(o, c) - l
    return body, rng, upper, lower


def candle_signals(df: pd.DataFrame) -> list[Signal]:
    if len(df) < 2:
        return []
    out: list[Signal] = []
    o, h, l, c = (df["open"].iloc[-1], df["high"].iloc[-1],
                  df["low"].iloc[-1], df["close"].iloc[-1])
    po, pc = df["open"].iloc[-2], df["close"].iloc[-2]
    body, rng, upper, lower = _parts(o, h, l, c)

    # Doji — indecision.
    if body <= 0.1 * rng:
        out.append(Signal("doji", Direction.NEUTRAL, 0.3,
                          "Doji — very small body, market indecision.", category="candle"))

    # Hammer — small body near top, long lower shadow (bullish in a downmove).
    if lower >= 2 * body and upper <= body and c >= o:
        out.append(Signal("hammer", Direction.BULL, 0.5,
                          "Hammer — long lower shadow, potential bullish reversal.", category="candle"))

    # Shooting star — small body near bottom, long upper shadow (bearish).
    if upper >= 2 * body and lower <= body and c <= o:
        out.append(Signal("shooting_star", Direction.BEAR, 0.5,
                          "Shooting star — long upper shadow, potential bearish reversal.", category="candle"))

    # Engulfing — current body fully engulfs previous opposite-color body.
    prev_bear = pc < po
    prev_bull = pc > po
    if c > o and prev_bear and c >= po and o <= pc:
        out.append(Signal("bullish_engulfing", Direction.BULL, 0.6,
                          "Bullish engulfing — up bar engulfs prior down bar.", category="candle"))
    if c < o and prev_bull and o >= pc and c <= po:
        out.append(Signal("bearish_engulfing", Direction.BEAR, 0.6,
                          "Bearish engulfing — down bar engulfs prior up bar.", category="candle"))

    # Harami — small body inside the previous large body (potential reversal/pause).
    if max(o, c) <= max(po, pc) and min(o, c) >= min(po, pc) and abs(po - pc) > body:
        direction = Direction.BULL if prev_bear else Direction.BEAR
        out.append(Signal("harami", direction, 0.35,
                          "Harami — small body inside prior body, momentum stalling.", category="candle"))
    return out
