"""Live buy-window confirmation math (deterministic, no AI).

Refines *entry timing only*: a signal that persists across consecutive 1-minute
readings — with price holding a key level on rising volume — is more trustworthy
than a single snapshot. If readings just oscillate, assurance stays low and the
UI correctly says "no added confidence — wait." It does NOT change the structural
multi-timeframe verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .analysis.engine import TimeframeReport
from .analysis.signals import Direction


@dataclass
class Tick:
    direction: str          # "bullish"/"bearish"/"neutral" (short-term bias)
    bias_score: float
    price: float
    holds_level: bool       # price above the buy key level (or below for sell)
    volume_rising: bool


@dataclass
class Assurance:
    pct: int                # 0..100 recency-weighted agreement with intended side
    agree: int
    total: int
    gate_ok: bool           # latest tick holds level + volume rising
    trend: str              # "rising" / "falling" / "flat"
    go: bool                # green light: pct>=threshold AND gate_ok
    message: str = ""


def make_tick(report: TimeframeReport, key_level: float | None, intended: Direction) -> Tick:
    bias = report.bias_score
    direction = (Direction.BULL if bias > 0.15 else
                 Direction.BEAR if bias < -0.15 else Direction.NEUTRAL)
    price = float(report.meta.get("last_close", 0.0))

    holds = True
    if key_level is not None and price > 0:
        holds = price >= key_level if intended == Direction.BULL else price <= key_level

    vol = report.df["volume"].dropna()
    volume_rising = False
    if len(vol) >= 6:
        recent = vol.iloc[-3:].mean()
        base = vol.iloc[-6:-3].mean()
        volume_rising = bool(base > 0 and recent > base)

    return Tick(direction.value, bias, price, holds, volume_rising)


def assess(ticks: list[Tick], intended: Direction, threshold: int = 65,
           window: int = 12) -> Assurance:
    if not ticks:
        return Assurance(0, 0, 0, False, "flat", False, "Waiting for the first reading…")

    recent = ticks[-window:]
    want = intended.value
    # Recency-weighted agreement: newest tick weighted highest.
    num = den = 0.0
    agree = 0
    for i, tk in enumerate(recent):
        w = 1.0 + i  # linear recency weight
        den += w
        if tk.direction == want:
            num += w
            agree += 1
    pct = round(num / den * 100) if den else 0

    gate_ok = bool(recent[-1].holds_level and recent[-1].volume_rising)

    # Trend of assurance: compare first vs second half agreement share.
    half = max(1, len(recent) // 2)
    early = sum(1 for tk in recent[:half] if tk.direction == want) / half
    late = sum(1 for tk in recent[half:] if tk.direction == want) / max(1, len(recent) - half)
    trend = "rising" if late > early + 0.05 else "falling" if late < early - 0.05 else "flat"

    go = pct >= threshold and gate_ok
    return Assurance(
        pct=pct, agree=agree, total=len(recent), gate_ok=gate_ok, trend=trend, go=go,
        message=_message(pct, agree, len(recent), gate_ok, trend, intended, go),
    )


def _message(pct, agree, total, gate_ok, trend, intended, go) -> str:
    side = "BUY" if intended == Direction.BULL else "SELL"
    arrow = {"rising": "↑", "falling": "↓", "flat": "→"}[trend]
    gate = ("holding the level on rising volume" if gate_ok
            else "level/volume not yet confirming")
    if go:
        head = f"✅ Confirmation building for {side}"
    elif pct >= 65:
        head = f"🟡 Direction agrees but {gate}"
    else:
        head = f"⏳ No added confidence yet — readings mixed"
    return f"{head}: {agree}/{total} recent readings support {side} → {pct}% {arrow} ({gate})."
