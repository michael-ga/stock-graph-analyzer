"""Volume confirmation (Murphy): volume should expand in the direction of the
trend; a breakout on heavy volume is more trustworthy; a volume climax can mark
exhaustion/reversal.
"""
from __future__ import annotations

import pandas as pd

from .signals import Direction, Signal


def volume_signals(df: pd.DataFrame, spike_mult: float = 1.8) -> list[Signal]:
    if "volume" not in df or df["volume"].fillna(0).sum() == 0:
        return []
    vol = df["volume"]
    avg = vol.rolling(20, min_periods=5).mean()
    if avg.dropna().empty:
        return []

    last_vol = float(vol.iloc[-1])
    last_avg = float(avg.iloc[-1])
    if last_avg <= 0:
        return []

    ratio = last_vol / last_avg
    out: list[Signal] = []
    if ratio >= spike_mult:
        # Direction of the bar tells us whether the surge confirms buyers or sellers.
        bar_up = df["close"].iloc[-1] >= df["open"].iloc[-1]
        direction = Direction.BULL if bar_up else Direction.BEAR
        out.append(Signal(
            "volume_spike", direction, min(1.0, 0.3 + 0.2 * ratio),
            f"Volume {ratio:.1f}x the 20-bar average on a "
            f"{'up' if bar_up else 'down'} bar — move is volume-confirmed.",
            category="volume", meta={"ratio": round(ratio, 2)},
        ))
    return out


def confirms_breakout(df: pd.DataFrame, spike_mult: float = 1.5) -> bool:
    """Whether the latest bar's volume confirms a breakout (used by pattern logic)."""
    if "volume" not in df:
        return False
    vol = df["volume"]
    avg = vol.rolling(20, min_periods=5).mean()
    if avg.dropna().empty or avg.iloc[-1] <= 0:
        return False
    return float(vol.iloc[-1]) / float(avg.iloc[-1]) >= spike_mult
