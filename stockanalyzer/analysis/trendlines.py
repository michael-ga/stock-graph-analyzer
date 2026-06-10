"""Trendlines & channels: least-squares lines through swing lows (up trendline)
and swing highs (down trendline). A close beyond a trendline by a noise filter is
a primary trend-change trigger (Murphy).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import atr
from .signals import Direction, Signal
from .trend import find_swings


@dataclass
class TrendLine:
    slope: float
    intercept: float
    kind: str          # "support" (through lows) or "resistance" (through highs)
    points: int

    def value_at(self, idx: int) -> float:
        return self.slope * idx + self.intercept


def _fit(points: list[tuple[int, float]]) -> tuple[float, float] | None:
    if len(points) < 2:
        return None
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope), float(intercept)


def fit_trendlines(df: pd.DataFrame, order: int = 3) -> dict[str, TrendLine]:
    swings = find_swings(df, order=order)
    lows = [(s.idx, s.price) for s in swings if s.kind == "low"][-3:]
    highs = [(s.idx, s.price) for s in swings if s.kind == "high"][-3:]

    out: dict[str, TrendLine] = {}
    fit_low = _fit(lows)
    if fit_low:
        out["support"] = TrendLine(fit_low[0], fit_low[1], "support", len(lows))
    fit_high = _fit(highs)
    if fit_high:
        out["resistance"] = TrendLine(fit_high[0], fit_high[1], "resistance", len(highs))
    return out


def trendline_break_signals(df: pd.DataFrame, lines: dict[str, TrendLine]) -> list[Signal]:
    if not lines:
        return []
    last_idx = len(df) - 1
    last_close = df["close"].iloc[-1]
    a = atr(df["high"], df["low"], df["close"], min(14, max(2, len(df) // 2)))
    filt = float(a.dropna().iloc[-1]) if a.notna().any() else last_close * 0.01

    out: list[Signal] = []
    sup = lines.get("support")
    if sup is not None and sup.points >= 2:
        line_val = sup.value_at(last_idx)
        if last_close < line_val - filt:
            out.append(Signal(
                "uptrend_line_break", Direction.BEAR, 0.7,
                f"Close {last_close:.2f} broke below the rising support trendline "
                f"(~{line_val:.2f}) — uptrend may be ending.",
                category="trendline",
            ))
    res = lines.get("resistance")
    if res is not None and res.points >= 2:
        line_val = res.value_at(last_idx)
        if last_close > line_val + filt:
            out.append(Signal(
                "downtrend_line_break", Direction.BULL, 0.7,
                f"Close {last_close:.2f} broke above the falling resistance trendline "
                f"(~{line_val:.2f}) — downtrend may be ending.",
                category="trendline",
            ))
    return out
