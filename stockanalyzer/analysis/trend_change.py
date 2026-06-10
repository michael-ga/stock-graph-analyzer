"""The headline feature: aggregate reversal triggers into a single trend-change
confidence score per timeframe, listing the contributing reasons.

Triggers (Murphy's classic reversal evidence): trendline break, MA cross,
oscillator divergence, reversal chart pattern, candlestick reversal, volume climax.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .signals import Direction, Signal

# Signals that specifically warn of a *change* in trend (vs. trend continuation).
REVERSAL_NAMES = {
    "uptrend_line_break", "downtrend_line_break",
    "golden_cross", "death_cross",
    "rsi_bearish_divergence", "rsi_bullish_divergence",
    "double_top", "double_bottom",
    "head_and_shoulders", "inverse_head_and_shoulders",
    "bullish_engulfing", "bearish_engulfing",
    "hammer", "shooting_star",
    "premarket_gap_up", "premarket_gap_down",
    "afterhours_move_up", "afterhours_move_down",
}


@dataclass
class TrendChange:
    likely: bool
    direction: Direction          # the direction the trend may be changing TOWARD
    score: float                  # 0..1 confidence
    reasons: list[str] = field(default_factory=list)


def assess_trend_change(current_trend: Signal, signals: list[Signal]) -> TrendChange:
    triggers = [s for s in signals if s.name in REVERSAL_NAMES]
    if not triggers:
        return TrendChange(False, Direction.NEUTRAL, 0.0, [])

    # A reversal that opposes the current trend is the meaningful case.
    opposing = [s for s in triggers
                if s.direction != Direction.NEUTRAL and s.direction != current_trend.direction]
    pool = opposing or triggers

    bull = sum(s.strength for s in pool if s.direction == Direction.BULL)
    bear = sum(s.strength for s in pool if s.direction == Direction.BEAR)
    if bull == bear == 0:
        return TrendChange(False, Direction.NEUTRAL, 0.0, [])

    direction = Direction.BULL if bull >= bear else Direction.BEAR
    dominant = max(bull, bear)
    # Diminishing-returns squashing so 1 strong trigger ~0.5, several stack toward 1.
    score = min(1.0, dominant / (dominant + 1.0) + 0.1 * (len(pool) - 1))
    reasons = [s.evidence for s in pool if s.direction == direction]

    return TrendChange(
        likely=score >= 0.5,
        direction=direction,
        score=round(score, 3),
        reasons=reasons,
    )
