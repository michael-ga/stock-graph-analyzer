"""Reverse DCF (plan §6.4) — what starting growth is the market pricing in?

Same model, same fade, same ``r`` as the forward DCF; solve for ``g0`` so the
per-share value equals today's price. This is the "second-level thinking" core:
it doesn't require trusting our point estimate, it turns valuation into a
checkable question — does the price need 30%/yr for a decade?
"""
from __future__ import annotations

from . import config as default_cfg
from .dcf import DCFSetup, dcf
from .models import Reason


def implied_growth(setup: DCFSetup, price: float, r: float, g_t: float,
                   cfg=default_cfg) -> tuple[float | None, list[Reason]]:
    lo, hi = cfg.REV_DCF_BOUNDS
    v_lo = dcf(setup, lo, r, g_t, cfg).per_share
    v_hi = dcf(setup, hi, r, g_t, cfg).per_share
    if v_hi < v_lo:                                   # value must rise with growth
        return None, [Reason("REV_DCF_NOT_MONOTONIC", 0,
                             "value falls as growth rises (loss-making model) — "
                             "implied growth undefined")]
    if price >= v_hi:
        return hi, [Reason("IMPLIED_BEYOND_RANGE", 0,
                           f"price exceeds the value even at {hi:.0%}/yr growth")]
    if price <= v_lo:
        return lo, [Reason("IMPLIED_BEYOND_RANGE", 0,
                           f"price is below the value even at {lo:.0%}/yr growth")]
    for _ in range(cfg.REV_DCF_MAX_ITER):
        mid = (lo + hi) / 2
        v = dcf(setup, mid, r, g_t, cfg).per_share
        if abs(v - price) <= cfg.REV_DCF_TOL * price:
            return mid, []
        lo, hi = (mid, hi) if v < price else (lo, mid)
    return (lo + hi) / 2, []


def gap_flag(gap: float | None, cfg=default_cfg) -> str | None:
    if gap is None:
        return None
    if gap > cfg.GAP_PFP:
        return "PRICED_FOR_PERFECTION"
    if gap > cfg.GAP_STRETCHED:
        return "STRETCHED"
    if gap <= cfg.GAP_PESSIMISTIC:
        return "MARKET_PESSIMISTIC"
    return None
