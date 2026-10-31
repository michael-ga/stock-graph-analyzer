"""Archetype routing (plan §5): one valuation method doesn't fit every business."""
from __future__ import annotations

from statistics import pstdev

from . import config as default_cfg
from .models import FundamentalSnapshot

ARCHETYPES = ("UNSUPPORTED", "NEW_LISTING", "REIT", "FINANCIAL", "PRE_REVENUE",
              "CYCLICAL", "GROWTH_UNPROFITABLE", "GROWTH_PROFITABLE", "MATURE", "FALLBACK")


def revenue_growth(s: FundamentalSnapshot) -> float | None:
    """TTM YoY, else the 3y CAGR."""
    if s.revenue_ttm and s.revenue_ttm_1y_ago and s.revenue_ttm_1y_ago > 0:
        return s.revenue_ttm / s.revenue_ttm_1y_ago - 1
    return s.revenue_cagr_3y


def sector(s: FundamentalSnapshot, cfg=default_cfg) -> str:
    return cfg.SECTOR_BY_SIC.get(s.sic or "", "default")


def classify(s: FundamentalSnapshot, cfg=default_cfg) -> tuple[str, str]:
    """(archetype, why) — first match wins, in the plan's order."""
    if s.unsupported_reason:
        return "UNSUPPORTED", s.unsupported_reason
    if s.quarters_available < cfg.MIN_QUARTERS:
        return "NEW_LISTING", f"only {s.quarters_available} quarters of filings"
    sic = int(s.sic) if (s.sic or "").isdigit() else None
    if s.sic in cfg.REIT_SIC:
        return "REIT", "REIT (needs FFO, not a standard XBRL tag)"
    lo, hi = cfg.FINANCIAL_SIC_RANGE
    if sic is not None and lo <= sic <= hi:
        return "FINANCIAL", f"financial SIC {s.sic} — debt is raw material, not financing"
    if s.revenue_ttm is None or s.revenue_ttm < cfg.PRE_REVENUE_USD:
        return "PRE_REVENUE", "revenue under $50M — no cash flows to value yet"
    margins = s.op_margin_history
    if s.sic in cfg.CYCLICAL_SIC or (len(margins) >= 3
                                    and pstdev(margins) > cfg.CYCLICAL_MARGIN_STD):
        why = (f"cyclical SIC {s.sic}" if s.sic in cfg.CYCLICAL_SIC
               else "operating margin swings year to year")
        return "CYCLICAL", why
    g = revenue_growth(s)
    if g is not None and g >= cfg.GROWTH_THRESHOLD and s.fcf_ttm is not None:
        if s.fcf_ttm < 0:
            return "GROWTH_UNPROFITABLE", f"revenue +{g:.0%}/yr, burning cash"
        return "GROWTH_PROFITABLE", f"revenue +{g:.0%}/yr, cash-generative"
    positive = sum(1 for f in s.fcf_ttm_history if f > 0)
    if len(s.fcf_ttm_history) >= 3 and positive >= 3:
        return "MATURE", "positive free cash flow in ≥3 of the last 4 TTM windows"
    return "FALLBACK", "free cash flow mostly negative with low growth"
