"""Afternoon base breakout — long, after a morning flush has stopped making lows.

The idea (SNDQ, 2026-10-07): the ETF flushed -11% by 12:20, then traded a tight
$10.62-10.88 range for 2.5 hours without a new low, and broke up through the top
of that range at 15:00 to close +2.1% off the base. None of the live reversal
bots can take that: they only buy a touch of support (within 0.4%) in the lower
35% of the day's range, and a breakout is price moving *away* from support.

Rules (pre-registered 2026-11-01; see ``algolab/LEARNINGS.md``, hypothesis H10):
- Flush: the session low so far (before the trigger bar) is at least 5% under the
  day's open and was made no later than the 13:00 bar.
- Base: the bars after that low. At least 18 of them (90 minutes) with no new low
  (true by construction once the low is the session low), a range of at most 3%
  of the base low, and higher lows: the lowest low of the second half is not under
  the lowest low of the first half.
- Trigger: a bar between 14:00 and 15:25 (labels; it closes by 15:30) whose
  *close* is above the base high. Only the first such close of the day fires; a
  later higher close is a chase, not a new setup.
- Entry: that bar's close.
- Stop: the base midpoint, 0.1% under. (Not the base low: for a base-height target
  that would never be 1R away.)
- Target: 1R (variant ``pm-base-brk-1r``) or 2R (``pm-base-brk-2r``).
- Exit: harness (stop-first, flat at the 15:55 bar close). One trade per variant
  per day.

No daily-bias filter on purpose: a flush day is against the trend by definition.
Adding one later is a new variant, pre-registered before it is run.

Not modelled: the MACD / RSI / SMA20-SMA50 turn visible on the SNDQ chart (too many
knobs for 40 sessions), a volume expansion on the breakout (bar volume against a
trailing mean is a time-of-day clock, LEARNINGS 2026-10-21), news.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .base import STOP_BUFFER, SetupContext, SetupDecision, fire, hhmm, no

NAME = "base_breakout"


@dataclass(frozen=True)
class BaseBreakoutConfig:
    name: str
    flush_pct: float = 0.05         # session low at least 5% under the open ...
    flush_by: str = "13:00"         # ... made no later than this bar label
    min_base_bars: int = 18         # 90 minutes since the low without a new low
    max_base_range: float = 0.03    # base high-low at most 3% of the base low
    trigger_start: str = "14:00"    # first bar label that may break out
    trigger_end: str = "15:25"      # last one (it closes 15:30)
    target_r: float = 1.0


VARIANTS: list[BaseBreakoutConfig] = [
    BaseBreakoutConfig("pm-base-brk-1r"),
    BaseBreakoutConfig("pm-base-brk-2r", target_r=2.0),
]


def signal(ctx: SetupContext, cfg: BaseBreakoutConfig) -> SetupDecision:
    s = ctx.session
    if s is None or len(s) < cfg.min_base_bars + 3:
        return no(cfg, NAME, "not enough session bars")
    t, now = hhmm(s.index), len(s) - 1
    if not (cfg.trigger_start <= t[now] <= cfg.trigger_end):
        return no(cfg, NAME, f"outside the breakout window ({cfg.trigger_start}-{cfg.trigger_end} bars)")
    o, h, l, c = (s[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))

    low_i = int(np.argmin(l[:now]))                  # first bar of the session low so far
    flush = 1 - l[low_i] / o[0]
    if flush < cfg.flush_pct:
        return no(cfg, NAME, f"no flush: the low is only {flush * 100:.1f}% under the open "
                  f"(need ≥ {cfg.flush_pct * 100:g}%)")
    if t[low_i] > cfg.flush_by:
        return no(cfg, NAME, f"the low came at {t[low_i]}, after {cfg.flush_by}: still making lows")
    first = low_i + 1
    n_base = now - first
    if n_base < cfg.min_base_bars:
        return no(cfg, NAME, f"base only {n_base} bars since the {t[low_i]} low "
                  f"(need ≥ {cfg.min_base_bars})")
    base_high, base_low = float(h[first:now].max()), float(l[first:now].min())
    if (base_high - base_low) / base_low > cfg.max_base_range:
        return no(cfg, NAME, f"base range {(base_high / base_low - 1) * 100:.1f}% "
                  f"(need ≤ {cfg.max_base_range * 100:g}%)")
    half = first + n_base // 2
    if l[first:half].min() > l[half:now].min():
        return no(cfg, NAME, "base is making lower lows, not higher lows")
    if c[now] <= base_high:
        return no(cfg, NAME, f"waiting for a close above the base high ${base_high:.2f}")
    for k in range(first + cfg.min_base_bars, now):  # only the first breakout close of the day
        if t[k] >= cfg.trigger_start and c[k] > h[first:k].max():
            return no(cfg, NAME, f"already broke out at {t[k]}: this is a chase")

    entry = float(c[now])
    stop = (base_high + base_low) / 2 * (1 - STOP_BUFFER)
    return fire(cfg, NAME, f"closed above the {t[first]}-{t[now - 1]} base high ${base_high:.2f} "
                f"after a {flush * 100:.0f}% flush",
                entry, stop, entry + cfg.target_r * (entry - stop))
