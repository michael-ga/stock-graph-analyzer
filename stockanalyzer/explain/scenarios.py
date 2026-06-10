"""Practical, rule-based "what could happen next" scenarios from the engine's
support/resistance levels. NOT predictions of certainty — deterministic geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.engine import TimeframeReport
from ..analysis.signals import Direction


@dataclass
class Scenario:
    upside: str = ""
    downside: str = ""
    upside_levels: list[float] = field(default_factory=list)
    downside_levels: list[float] = field(default_factory=list)
    ordered: list[str] = field(default_factory=list)   # lead with the trend direction


def _pct(frm: float, to: float) -> float:
    return (to / frm - 1.0) * 100 if frm else 0.0


def build_scenario(report: TimeframeReport) -> Scenario:
    price = float(report.meta.get("last_close", 0.0))
    if price <= 0 or not report.levels:
        return Scenario("Not enough level data to sketch scenarios.", "")

    resistances = sorted((lv.price for lv in report.levels if lv.price > price))
    supports = sorted((lv.price for lv in report.levels if lv.price < price), reverse=True)

    sc = Scenario()
    sc.upside_levels = resistances[:2]
    sc.downside_levels = supports[:2]

    if resistances:
        r1 = resistances[0]
        nxt = f", then ~${resistances[1]:.2f} (+{_pct(price, resistances[1]):.1f}%)" if len(resistances) > 1 else ""
        sc.upside = (f"If it breaks above ~${r1:.2f} (+{_pct(price, r1):.1f}%), "
                     f"the next ceiling to watch is there{nxt}.")
    else:
        sc.upside = "Price is near its highest levels on this range — no clear ceiling above."

    if supports:
        s1 = supports[0]
        nxt = f", then ~${supports[1]:.2f} ({_pct(price, supports[1]):.1f}%)" if len(supports) > 1 else ""
        sc.downside = (f"If it drops below ~${s1:.2f} ({_pct(price, s1):.1f}%), "
                       f"the next floor to watch is there{nxt}.")
    else:
        sc.downside = "Price is near its lowest levels on this range — no clear floor below."

    # Lead with the direction that matches the prevailing trend.
    if report.trend.direction == Direction.BEAR:
        sc.ordered = [sc.downside, sc.upside]
    else:
        sc.ordered = [sc.upside, sc.downside]
    return sc
