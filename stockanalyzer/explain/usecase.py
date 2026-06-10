"""The user's intent, used to tailor the recommendation wording and go/no-go."""
from __future__ import annotations

from enum import Enum


class UseCase(str, Enum):
    BUY = "buy"      # thinking of buying
    SELL = "sell"    # thinking of selling
    OWN = "own"      # already own it, managing the position

    @property
    def label(self) -> str:
        return {
            UseCase.BUY: "I'm thinking of buying",
            UseCase.SELL: "I'm thinking of selling",
            UseCase.OWN: "I already own it",
        }[self]
