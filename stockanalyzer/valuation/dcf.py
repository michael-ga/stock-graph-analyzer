"""Discount rate, plausible growth and the forward DCF (plan §6.1–6.3).

All pure functions of their arguments. The DCF itself (``dcf``) knows nothing
about snapshots — ``setup_for`` maps a snapshot + archetype to its inputs — so
the golden/round-trip tests can drive it with hand-computed toy numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import config as default_cfg
from .classify import revenue_growth, sector
from .models import FundamentalSnapshot, Reason


@dataclass(frozen=True)
class DCFSetup:
    mode: str                  # "fcf" | "revenue"
    base: float                # FCF_0 (fcf mode) or Rev_0 (revenue mode)
    cash: float
    debt: float
    shares: float
    m0: float = 0.0            # revenue mode: current FCF margin
    m_target: float = 0.0      # revenue mode: margin reached by MARGIN_RAMP_YEARS


@dataclass(frozen=True)
class DCFValue:
    per_share: float
    ev: float
    equity: float
    tv_share: float            # PV(terminal) / EV


def discount_rate(s: FundamentalSnapshot, price: float, rf: float,
                  cfg=default_cfg) -> tuple[float, float, list[Reason]]:
    """(r, g_terminal, reasons) — tiered heuristic, not CAPM on noisy beta."""
    reasons: list[Reason] = []
    r = rf + cfg.ERP
    mcap = price * s.shares_out if s.shares_out else None
    if mcap is not None and mcap < cfg.SMALL_CAP_USD:
        r += cfg.SMALL_CAP_PREMIUM
        reasons.append(Reason("R_SMALL_CAP", 0, f"+{cfg.SMALL_CAP_PREMIUM:.1%} small-cap premium"))
    if s.fcf_ttm is not None and s.fcf_ttm < 0:
        r += cfg.NEG_FCF_PREMIUM
        reasons.append(Reason("R_NEG_FCF", 0, f"+{cfg.NEG_FCF_PREMIUM:.1%} for negative FCF"))
    if s.ebitda_ttm and s.ebitda_ttm > 0 and s.total_debt is not None:
        nd = s.total_debt - (s.cash_st or 0.0)
        if nd / s.ebitda_ttm > cfg.LEVERAGE_WARN:
            r += cfg.HIGH_LEVERAGE_PREMIUM
            reasons.append(Reason("R_LEVERAGE", 0,
                                  f"+{cfg.HIGH_LEVERAGE_PREMIUM:.1%} for net debt > 3× EBITDA"))
    r = min(max(r, cfg.R_MIN), cfg.R_MAX)
    g_t = min(cfg.G_TERMINAL_MAX, rf)
    if r - g_t < cfg.MIN_R_MINUS_G:
        r = g_t + cfg.MIN_R_MINUS_G
    return r, g_t, reasons


def plausible_growth(s: FundamentalSnapshot, cfg=default_cfg,
                     guidance: float | None = None) -> tuple[float | None, list[Reason]]:
    """Blend of track record, capped by what companies this size sustain."""
    parts = {"cagr_3y": s.revenue_cagr_3y, "yoy": revenue_growth(s)
             if s.revenue_ttm_1y_ago else None, "guidance": guidance}
    avail = {k: v for k, v in parts.items() if v is not None}
    if not avail:
        return None, [Reason("NO_GROWTH_HISTORY", 0, "no revenue history to anchor growth")]
    wsum = sum(cfg.PLAUSIBLE_WEIGHTS[k] for k in avail)
    g = sum(cfg.PLAUSIBLE_WEIGHTS[k] * v for k, v in avail.items()) / wsum
    cap = next(c for floor, c in cfg.BASE_RATE_CAP if (s.revenue_ttm or 0) > floor or floor == 0)
    reasons = []
    if g > cap:
        reasons.append(Reason("GROWTH_CAPPED", 0,
                              f"track record {g:.0%}/yr capped at {cap:.0%} — companies this "
                              "size rarely sustain more"))
        g = cap
    return max(g, cfg.GROWTH_FLOOR), reasons


def growth_path(g0: float, g_t: float, n: int) -> list[float]:
    """Linear fade from g0 (year 1) to g_terminal (year n)."""
    if n == 1:
        return [g0]
    return [g0 + (g_t - g0) * (t - 1) / (n - 1) for t in range(1, n + 1)]


def dcf(setup: DCFSetup, g0: float, r: float, g_t: float, cfg=default_cfg,
        horizon: int | None = None) -> DCFValue:
    n = horizon or cfg.HORIZON_YEARS
    ramp = cfg.MARGIN_RAMP_YEARS
    pv = 0.0
    level = setup.base
    fcf_t = 0.0
    for t, g in enumerate(growth_path(g0, g_t, n), start=1):
        level *= 1 + g
        if setup.mode == "revenue":
            m = setup.m0 + (setup.m_target - setup.m0) * min(t, ramp) / ramp
            fcf_t = level * m
        else:
            fcf_t = level
        pv += fcf_t / (1 + r) ** t
    tv = fcf_t * (1 + g_t) / (r - g_t)
    pv_tv = tv / (1 + r) ** n
    ev = pv + pv_tv
    equity = ev + setup.cash - setup.debt
    per_share = max(equity, 0.0) / setup.shares if setup.shares else 0.0
    return DCFValue(per_share, ev, equity, pv_tv / ev if ev > 0 else 1.0)


def setup_for(s: FundamentalSnapshot, archetype: str,
              cfg=default_cfg) -> tuple[DCFSetup | None, list[Reason]]:
    """Map a snapshot to DCF inputs for its archetype (§6.2)."""
    if not s.shares_out or s.revenue_ttm is None:
        return None, [Reason("DCF_NO_INPUTS", 0, "missing shares or revenue — no DCF")]
    sbc = (s.sbc_ttm or 0.0) if cfg.SBC_AS_EXPENSE else 0.0
    common = dict(cash=s.cash_st or 0.0, debt=s.total_debt or 0.0, shares=s.shares_out)
    target = cfg.TARGET_FCF_MARGIN.get(sector(s, cfg), cfg.TARGET_FCF_MARGIN["default"])
    if archetype == "CYCLICAL" and s.fcf_margin_avg is not None:
        base = s.revenue_ttm * s.fcf_margin_avg
        if base > 0:
            return DCFSetup("fcf", base, **common), [Reason(
                "DCF_NORMALIZED", 0,
                f"cyclical: FCF normalized to the {s.fcf_margin_avg:.0%} average margin, "
                "not this year's")]
    if archetype in ("MATURE", "GROWTH_PROFITABLE", "CYCLICAL") and s.fcf_ttm is not None:
        fcf0 = s.fcf_ttm - sbc
        if fcf0 > 0:
            return DCFSetup("fcf", fcf0, **common), []
    if archetype in ("GROWTH_UNPROFITABLE", "GROWTH_PROFITABLE", "MATURE", "CYCLICAL") \
            and s.revenue_ttm > 0:
        m0 = ((s.fcf_ttm or 0.0) - sbc) / s.revenue_ttm
        return DCFSetup("revenue", s.revenue_ttm, m0=m0, m_target=target, **common), [
            Reason("DCF_REVENUE_MODE", 0,
                   f"revenue-driven DCF: FCF margin {m0:.0%} ramps to {target:.0%} "
                   f"by year {cfg.MARGIN_RAMP_YEARS}")]
    return None, [Reason("DCF_NOT_APPLICABLE", 0, "free cash flow ≤ 0 and no revenue path")]


def shift_margin(setup: DCFSetup, delta: float, revenue: float) -> DCFSetup:
    """Scenario lever: ±pp of FCF margin (target margin in revenue mode)."""
    if setup.mode == "revenue":
        return replace(setup, m_target=setup.m_target + delta)
    return replace(setup, base=max(setup.base + delta * revenue, 1e-9))
