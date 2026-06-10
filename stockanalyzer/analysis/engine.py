"""Orchestrates every detector over one timeframe into a TimeframeReport."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .candles import candle_signals
from .indicator_signals import (
    ma_signals,
    macd_signals,
    rsi_signals,
    stochastic_signals,
)
from .indicators import add_indicators
from .levels import Level, find_levels, level_signals
from .patterns import pattern_signals
from .premarket import extended_session_signal
from .signals import Direction, Signal
from .trend import detect_trend
from .trend_change import TrendChange, assess_trend_change
from .trendlines import TrendLine, fit_trendlines, trendline_break_signals
from .volume import volume_signals


@dataclass
class TimeframeReport:
    df: pd.DataFrame                       # frame with indicator columns attached
    trend: Signal
    signals: list[Signal]
    levels: list[Level]
    trendlines: dict[str, TrendLine]
    trend_change: TrendChange
    bias_score: float = 0.0                # -1 (bearish) .. +1 (bullish)
    meta: dict = field(default_factory=dict)

    @property
    def bias_direction(self) -> Direction:
        if self.bias_score > 0.15:
            return Direction.BULL
        if self.bias_score < -0.15:
            return Direction.BEAR
        return Direction.NEUTRAL


# Per-category weights: the prevailing trend is the backbone (Murphy), chart
# patterns and trendline breaks are significant, oscillators/levels/candles are
# secondary timing inputs. This keeps a strong trend from being drowned out by a
# cluster of transient momentum signals at a short-term extreme.
CATEGORY_WEIGHTS: dict[str, float] = {
    "trend": 2.0,
    "pattern": 1.6,
    "trendline": 1.5,
    "momentum": 0.8,
    "level": 0.8,
    "volume": 0.7,
    "candle": 0.6,
    "session": 1.0,
}


def _bias(signals: list[Signal]) -> float:
    """Category- and strength-weighted average direction across signals → -1..+1."""
    num = den = 0.0
    for s in signals:
        w = CATEGORY_WEIGHTS.get(s.category, 1.0) * s.strength
        num += s.direction.sign * w
        den += w
    return round(num / den, 3) if den else 0.0


def analyze_timeframe(df: pd.DataFrame, order: int = 3) -> TimeframeReport:
    """Run the full engine on a normalized OHLCV frame."""
    enriched = add_indicators(df)

    trend = detect_trend(df, order=order)
    levels = find_levels(df, order=order)
    lines = fit_trendlines(df, order=order)

    signals: list[Signal] = [trend]
    signals += level_signals(df, levels)
    signals += trendline_break_signals(df, lines)
    signals += ma_signals(enriched)
    signals += rsi_signals(enriched)
    signals += macd_signals(enriched)
    signals += stochastic_signals(enriched)
    signals += volume_signals(df)
    signals += candle_signals(df)
    signals += pattern_signals(df, order=order)
    ext = extended_session_signal(df)   # pre-market / after-hours move (intraday only)
    if ext is not None:
        signals.append(ext)

    trend_change = assess_trend_change(trend, signals)
    bias = _bias(signals)

    return TimeframeReport(
        df=enriched,
        trend=trend,
        signals=signals,
        levels=levels,
        trendlines=lines,
        trend_change=trend_change,
        bias_score=bias,
        meta={"last_close": float(df["close"].iloc[-1]), "bars": len(df)},
    )
