"""The common output type for every detector, so the verdict layer can aggregate
and explain uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    BULL = "bullish"
    BEAR = "bearish"
    NEUTRAL = "neutral"

    @property
    def sign(self) -> int:
        return {Direction.BULL: 1, Direction.BEAR: -1, Direction.NEUTRAL: 0}[self]


@dataclass
class Signal:
    """One piece of technical evidence.

    name:      detector identifier, e.g. "rsi", "double_top"
    direction: bullish / bearish / neutral
    strength:  0..1 confidence/importance, used as the aggregation weight
    evidence:  human-readable reason shown in the explanation
    category:  grouping for the report ("trend", "momentum", "pattern", ...)
    """

    name: str
    direction: Direction
    strength: float
    evidence: str
    category: str = "general"
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.strength = max(0.0, min(1.0, float(self.strength)))
