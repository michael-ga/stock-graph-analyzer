"""Every trader in the virtual book, grouped for the Info tab.

``virtualbook.TRADER_RULES`` says what each trader does; this module says which
kind of trading it is and whether it is still live. A test fails when a trader is
added to ``TRADER_RULES`` without being classified here, so the Info tab can never
silently miss a bot.
"""
from __future__ import annotations

from dataclasses import dataclass

from stockanalyzer.virtualbook import TRADER_RULES

DAY = "day"
LONG = "long"
MANUAL = "manual"

CATEGORIES = (DAY, LONG, MANUAL)
CATEGORY_LABELS = {
    DAY: "Day trade",
    LONG: "Long term (swing, held for days)",
    MANUAL: "Manual",
}

# Intraday bots enter and exit within one session (horizon 1 day); the swing-plan bots
# hold for days (horizon 3 days). "me" is the owner's own 📒 Virtual BUY button.
CATEGORY_OF = {
    "me": MANUAL,
    "bot-GO": LONG,
    "bot-70": LONG,
    "bot-BRK": LONG,
    "bot-gap-fixed": DAY,
    "bot-gap-mgd": DAY,
    "rev-trend-touch": DAY,
    "rev-trend-confirm": DAY,
    "rev-strong-touch": DAY,
    "rev-strong-confirm": DAY,
    "rev-trend-vol": DAY,
    "rev-strong-vol": DAY,
    "sb-am-bias": DAY,
    "sb-mkt": DAY,
    "xfade-dbl-bottom": DAY,
    "xfade-chop": DAY,
}

# No longer open new trades. Their open positions are still marked and managed
# until they close, so they stay in the book.
RETIRED = frozenset({
    "bot-BRK", "bot-gap-fixed", "rev-trend-touch", "rev-strong-confirm",
})


@dataclass(frozen=True)
class Trader:
    name: str
    category: str
    live: bool
    rule: str


def registry() -> list[Trader]:
    """All traders, in ``TRADER_RULES`` order."""
    return [
        Trader(name=name, category=CATEGORY_OF.get(name, DAY),
               live=name not in RETIRED, rule=rule)
        for name, rule in TRADER_RULES.items()
    ]


def category_of(name: str) -> str:
    """The category of a trader; a trader this module doesn't know counts as day trade."""
    return CATEGORY_OF.get(name, DAY)


def by_category(category: str, *, live: bool | None = None) -> list[Trader]:
    return [t for t in registry()
            if t.category == category and (live is None or t.live == live)]
