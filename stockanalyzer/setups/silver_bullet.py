"""ICT "Silver Bullet" — long form, first hour of the regular session.

The source idea is bearish (sell a bearish fair-value gap in premium, aim at
sell-side liquidity). The book is long-only, so this is the mirror; on a pair of
daily 2x ETFs the inverse ETF supplies the other direction (long SNDQ = the
bearish SNDK Silver Bullet).

Rules (pre-registered 2026-10-25, screen in ``algolab/LEARNINGS.md``):
- Bias: the instrument's last daily close is above its 20-day mean.
- Fair-value gap: three consecutive 5-min bars where bar 3's low is above bar 1's
  high and bar 2 closed up. It must complete inside 09:30–10:30 ET, and its
  consequent encroachment (CE, the gap's midpoint) must sit in the discount half
  of the session's range so far. Only the first such gap of the day counts.
- Entry: a resting buy limit at the CE, filled by the first later bar that trades
  down to it, no later than the bar ending 10:30. Unfilled orders are cancelled.
- Stop: 0.1% under bar 2's low.
- Target: the nearest buy-side liquidity at least 1R above the CE: the session
  high when the gap formed, today's pre-market high, yesterday's high, or
  yesterday's 13:30–16:00 high. No such level, no trade.

Not modelled from the source: SMT divergence between NQ/ES/YM (meaningless for a
single-stock ETF), the 09:45 news pause (no calendar feed), and the "two visits
cancel the order" rule.
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import (MIN_RR, STOP_BUFFER, SetupContext, SetupDecision, daily_bias_up,
                   fire, hhmm, no)

NAME = "silver_bullet"


@dataclass(frozen=True)
class SilverBulletConfig:
    name: str
    window_start: str = "09:30"    # first bar label that may complete the gap
    window_end: str = "10:25"      # last bar label (it closes 10:30) to form or fill
    need_bias: bool = True


VARIANTS: list[SilverBulletConfig] = [SilverBulletConfig("sb-am-bias")]


def first_discount_fvg(o, h, l, c, t, cfg: SilverBulletConfig, upto: int):
    """Index ``k`` of bar 3 of the first bullish FVG completed at or before
    ``upto`` inside the window whose CE is in the discount half, with its CE."""
    for k in range(2, upto + 1):
        if not (cfg.window_start <= t[k] <= cfg.window_end):
            continue
        if not (l[k] > h[k - 2] and c[k - 1] > o[k - 1]):
            continue
        lo, hi = l[:k + 1].min(), h[:k + 1].max()
        ce = (h[k - 2] + l[k]) / 2
        if ce <= lo + 0.5 * (hi - lo):
            return k, ce
    return None


def signal(ctx: SetupContext, cfg: SilverBulletConfig) -> SetupDecision:
    s = ctx.session
    if s is None or len(s) < 4:
        return no(cfg, NAME, "not enough session bars")
    if cfg.need_bias and not daily_bias_up(ctx.daily):
        return no(cfg, NAME, "daily bias not up (close ≤ 20-day mean)")
    o, h, l, c = (s[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    t, now = hhmm(s.index), len(s) - 1
    if t[now] > cfg.window_end:
        return no(cfg, NAME, f"outside the Silver Bullet window (ends {cfg.window_end} bar)")

    gap = first_discount_fvg(o, h, l, c, t, cfg, now - 1)
    if gap is None:
        return no(cfg, NAME, "no bullish fair-value gap in discount yet")
    k, ce = gap
    if (l[k + 1:now] <= ce).any():
        return no(cfg, NAME, "the CE order already filled earlier today")
    if l[now] > ce:
        return no(cfg, NAME, f"waiting for a retrace to the CE ${ce:.2f}")

    stop = l[k - 1] * (1 - STOP_BUFFER)
    liquidity = [h[:k + 1].max()]
    if ctx.premarket is not None and len(ctx.premarket):
        liquidity.append(float(ctx.premarket["high"].max()))
    if ctx.prev_session is not None and len(ctx.prev_session):
        liquidity.append(float(ctx.prev_session["high"].max()))
        pm = ctx.prev_session.between_time("13:30", "15:55")
        if len(pm):
            liquidity.append(float(pm["high"].max()))
    risk = ce - stop
    above = sorted(x for x in liquidity if x - ce >= MIN_RR * risk - 1e-9)
    if not above:
        return no(cfg, NAME, "no buy-side liquidity ≥ 1R above the CE")
    return fire(cfg, NAME, f"filled the FVG CE ${ce:.2f} (gap at {t[k]}), "
                f"aim at liquidity ${above[0]:.2f}",
                float(ce), float(stop), float(above[0]), filled_intrabar=True)
