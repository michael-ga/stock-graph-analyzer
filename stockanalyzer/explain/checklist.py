"""Short, predefined 'what to watch next' steps with real numbers, tailored to the
user's use-case. Built deterministically from the engine's levels/RSI.
"""
from __future__ import annotations

from ..analysis.engine import TimeframeReport
from .usecase import UseCase


def build_checklist(report: TimeframeReport, usecase: UseCase) -> list[str]:
    price = float(report.meta.get("last_close", 0.0))
    items: list[str] = []
    if price <= 0:
        return items

    resistances = sorted((lv.price for lv in report.levels if lv.price > price))
    supports = sorted((lv.price for lv in report.levels if lv.price < price), reverse=True)
    r1 = resistances[0] if resistances else None
    s1 = supports[0] if supports else None

    if usecase in (UseCase.BUY, UseCase.OWN):
        if r1:
            items.append(f"✅ GO (strength) if a daily close clears ~${r1:.2f} on above-average volume.")
        if s1:
            tag = "🛑 NO-GO / exit risk" if usecase == UseCase.BUY else "🛑 Protect: consider a stop"
            items.append(f"{tag} if it closes below ~${s1:.2f} (nearest support).")
    else:  # SELL
        if s1:
            items.append(f"✅ GO (sell) if it closes below ~${s1:.2f} — support is breaking.")
        if r1:
            items.append(f"🟡 HOLD OFF selling if it pushes back above ~${r1:.2f} — strength returning.")

    # RSI timing note.
    rsi = report.df["rsi"].dropna()
    if not rsi.empty:
        r = float(rsi.iloc[-1])
        if r >= 70:
            items.append(f"👀 RSI is {r:.0f} (hot/overbought) — chasing here is risky; waiting for a cooldown is safer.")
        elif r <= 30:
            items.append(f"👀 RSI is {r:.0f} (cold/oversold) — a bounce is more likely than further drop short-term.")
        else:
            items.append(f"👀 RSI is {r:.0f} (neutral) — no extreme; let price confirm the level above/below.")

    return items
