"""Support & resistance: cluster swing points by price proximity, rank by number
of touches and recency (Murphy: a level tested more often is more significant).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import atr
from .signals import Direction, Signal
from .trend import find_swings


@dataclass
class Level:
    price: float
    touches: int
    kind: str          # "support" or "resistance"
    strength: float    # 0..1


def find_levels(df: pd.DataFrame, order: int = 3, max_levels: int = 6) -> list[Level]:
    swings = find_swings(df, order=order)
    if not swings:
        return []

    # Tolerance for "same level": a fraction of recent ATR (price-scale aware).
    a = atr(df["high"], df["low"], df["close"], min(14, max(2, len(df) // 2)))
    tol = float(a.dropna().iloc[-1]) if a.notna().any() else df["close"].iloc[-1] * 0.01
    tol = max(tol, df["close"].iloc[-1] * 0.005)

    n = len(df)
    clusters: list[dict] = []
    for s in swings:
        placed = False
        for c in clusters:
            if abs(s.price - c["price"]) <= tol:
                c["prices"].append(s.price)
                c["touches"] += 1
                c["last_idx"] = max(c["last_idx"], s.idx)
                c["price"] = sum(c["prices"]) / len(c["prices"])
                placed = True
                break
        if not placed:
            clusters.append(
                {"price": s.price, "prices": [s.price], "touches": 1, "last_idx": s.idx}
            )

    last_price = df["close"].iloc[-1]
    levels: list[Level] = []
    for c in clusters:
        recency = c["last_idx"] / max(1, n - 1)          # newer = closer to 1
        strength = min(1.0, 0.25 * c["touches"] + 0.4 * recency)
        kind = "resistance" if c["price"] >= last_price else "support"
        levels.append(Level(round(c["price"], 4), c["touches"], kind, round(strength, 3)))

    levels.sort(key=lambda lv: lv.strength, reverse=True)
    return levels[:max_levels]


def level_signals(df: pd.DataFrame, levels: list[Level]) -> list[Signal]:
    """Signal when price sits very close to a strong level (potential bounce/break)."""
    if not levels:
        return []
    last = df["close"].iloc[-1]
    a = atr(df["high"], df["low"], df["close"], min(14, max(2, len(df) // 2)))
    tol = float(a.dropna().iloc[-1]) if a.notna().any() else last * 0.01

    out: list[Signal] = []
    nearest = min(levels, key=lambda lv: abs(lv.price - last))
    dist = abs(nearest.price - last)
    if dist <= tol:
        if nearest.kind == "support":
            out.append(Signal(
                "near_support", Direction.BULL, nearest.strength,
                f"Price {last:.2f} is testing support ~{nearest.price:.2f} "
                f"({nearest.touches} touches) — potential bounce.",
                category="level",
            ))
        else:
            out.append(Signal(
                "near_resistance", Direction.BEAR, nearest.strength,
                f"Price {last:.2f} is testing resistance ~{nearest.price:.2f} "
                f"({nearest.touches} touches) — potential rejection.",
                category="level",
            ))
    return out
