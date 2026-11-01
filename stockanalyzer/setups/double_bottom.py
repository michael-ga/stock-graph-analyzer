"""Extension fade — double bottom after an overextended drop, long.

Rules (pre-registered 2026-10-25, screen in ``algolab/LEARNINGS.md``):
- Extension: a bar P1 between 09:50 and 14:30 ET makes the lowest low of its last
  7 bars, and the drop from the highest high of those bars to P1's low is at
  least 3x the median 5-min bar range of the 24 bars before P1 (a "sprint", not a
  grind). P1 must be a pivot: the next two bars make higher lows. Only the first
  such extension of the day counts.
- Double bottom: within 3–18 bars of P1 a second low P2 lands within 25% of the
  drop of P1's low (and the bar after it makes a higher low). If price undercuts
  P1 by more than that first, the pattern is void.
- Entry: a buy-stop at the neckline (the highest high between P1 and P2), filled
  by the first bar within 12 bars of P2 that trades up to it (at its open if it
  gapped over). A new low under both bottoms first voids it.
- Stop: 0.1% under the lower bottom. Target: 1R (a quick fade, not a reversal
  of the trend). One trade per day.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .base import STOP_BUFFER, SetupContext, SetupDecision, fire, hhmm, no

NAME = "double_bottom"


@dataclass(frozen=True)
class DoubleBottomConfig:
    name: str
    window_start: str = "09:50"     # first bar that may be the extension low P1
    window_end: str = "14:30"       # last one
    drop_bars: int = 6              # the drop into P1 happens within this many bars
    base_bars: int = 24             # "normal" bar = median range of this many bars
    extension_mult: float = 3.0     # drop ≥ 3x a normal bar = overextended
    tolerance: float = 0.25         # P2 within 25% of the drop of P1
    max_p2_bars: int = 18
    max_entry_bars: int = 12
    target_r: float = 1.0


VARIANTS: list[DoubleBottomConfig] = [DoubleBottomConfig("xfade-dbl-bottom")]


def find_entry(o, h, l, t, cfg: DoubleBottomConfig):
    """The first extension's double-bottom entry ``(e, entry, stop, p1, p2)`` in
    these bars, or ``None``. Uses only the bars it is given."""
    n = len(l)
    for p1 in range(n):
        if not (cfg.window_start <= t[p1] <= cfg.window_end):
            continue
        if p1 < 6:
            continue
        base = np.median((h - l)[max(0, p1 - cfg.base_bars):p1])
        if not base:
            continue
        a = max(0, p1 - cfg.drop_bars)
        drop = h[a:p1 + 1].max() - l[p1]
        if drop < cfg.extension_mult * base or l[p1] > l[a:p1 + 1].min():
            continue
        if p1 + 2 >= n or l[p1 + 1] <= l[p1] or l[p1 + 2] <= l[p1]:
            continue
        band = cfg.tolerance * drop
        for p2 in range(p1 + 3, min(p1 + cfg.max_p2_bars + 1, n - 1)):
            if l[p2] < l[p1] - band:
                break
            if abs(l[p2] - l[p1]) <= band and l[p2 + 1] > l[p2]:
                neck = h[p1 + 1:p2].max()
                floor = min(l[p1], l[p2])
                for e in range(p2 + 1, min(p2 + cfg.max_entry_bars + 1, n)):
                    if l[e] < floor:
                        break
                    if h[e] >= neck:
                        return e, max(neck, o[e]), floor * (1 - STOP_BUFFER), p1, p2
                break
        return None                      # only the first extension of the day
    return None


def signal(ctx: SetupContext, cfg: DoubleBottomConfig) -> SetupDecision:
    s = ctx.session
    if s is None or len(s) < 10:
        return no(cfg, NAME, "not enough session bars")
    o, h, l = (s[col].to_numpy(dtype=float) for col in ("open", "high", "low"))
    t = hhmm(s.index)
    hit = find_entry(o, h, l, t, cfg)
    if hit is None or hit[0] != len(s) - 1:
        return no(cfg, NAME, "no extension double bottom breaking its neckline now")
    _, entry, stop, p1, p2 = hit
    return fire(cfg, NAME, f"double bottom {t[p1]}/{t[p2]} broke its neckline",
                float(entry), float(stop), float(entry + cfg.target_r * (entry - stop)),
                filled_intrabar=True)
