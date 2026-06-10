"""Trading strategy dimension — orthogonal to the Buy/Sell/Own use-case.

Kept top-level (not under explain/ or verdict/) so both packages can import it
without creating a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Strategy(str, Enum):
    INVESTOR = "investor"   # long-term: weight long timeframes, breakout confirmation
    SWING = "swing"         # short-term: weight short timeframes, enter near reversals

    @property
    def label(self) -> str:
        return {
            Strategy.INVESTOR: "📈 Long-term investor",
            Strategy.SWING: "⚡ Swing trader",
        }[self]


class SwingPace(str, Enum):
    """How fast a swing to trade — selectable inside swing mode."""

    STANDARD = "standard"   # classic swing, days → ~2 weeks
    FAST = "fast"           # quick swing, 1–3 days, tighter stops/targets

    @property
    def label(self) -> str:
        return {
            SwingPace.STANDARD: "Standard (days–2wk)",
            SwingPace.FAST: "Fast (1–3 days)",
        }[self]


@dataclass(frozen=True)
class PaceTuning:
    """The honest 'horizon ladder' — everything scales from DAILY volatility.

    A swing target is capped by what the horizon can physically deliver
    (`dailyATR × √budget_days`) and by the next mapped resistance — never a
    fixed % floor (the old floor inflated targets straight through ceilings).
    The stop must survive overnight noise: at least `stop_atr_mult × dailyATR`.
    """

    stop_atr_mult: float   # stop distance ≥ this × daily ATR (overnight survival)
    budget_days: float     # trading days the move has — vol budget = ATR×√days
    target_cap: float      # absolute ceiling on a single target (fraction)
    min_move: float        # a swing must aim at least this (fraction) to be worth it
    earnings_guard_days: int   # block GO when earnings are within this many days
    horizon: str


# Scalar tuning per pace (timeframe ordering/weights live in recommend/aggregate).
SWING_PACE: dict[SwingPace, PaceTuning] = {
    SwingPace.STANDARD: PaceTuning(1.2, 10, 0.15, 0.05, 14, "days to ~2 weeks"),
    SwingPace.FAST: PaceTuning(0.7, 3, 0.12, 0.03, 3, "1–3 days"),
}
