"""Frozen result types for the valuation layer (plan §4).

Every engine result carries ``status``, ``confidence`` and ``reasons`` (R3) —
nothing downstream should ever see a precise-looking number without them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Status = Literal["OK", "LOW_CONFIDENCE", "INSUFFICIENT_DATA", "UNSUPPORTED"]
Label = Literal["UNDERVALUED", "FAIR", "OVERVALUED", "PRICED_FOR_PERFECTION", "CANT_VALUE"]


@dataclass(frozen=True)
class HistoryPoint:
    """TTM fundamentals at one past quarter end — feeds multiples vs own history."""
    end: date
    revenue: float | None
    ebit: float | None
    ebitda: float | None
    net_income: float | None
    fcf: float | None
    equity: float | None
    net_debt: float | None
    shares: float | None


@dataclass(frozen=True)
class FundamentalSnapshot:
    ticker: str
    cik: str
    as_of: date                          # built only from filings with filed <= as_of
    last_period_end: date | None
    quarters_available: int              # discrete quarters reconstructable
    sic: str | None
    revenue_ttm: float | None
    revenue_ttm_1y_ago: float | None
    revenue_cagr_3y: float | None
    gross_margin_ttm: float | None
    operating_margin_ttm: float | None
    ebit_ttm: float | None
    ebitda_ttm: float | None
    net_income_ttm: float | None
    cfo_ttm: float | None
    capex_ttm: float | None
    fcf_ttm: float | None                # cfo - capex
    sbc_ttm: float | None
    interest_expense_ttm: float | None
    cash_st: float | None
    total_debt: float | None
    equity: float | None
    shares_out: float | None
    diluted_shares_yoy: float | None     # fractional change
    fcf_ttm_history: tuple[float, ...] = ()        # last 4 TTM windows, newest last
    op_margin_history: tuple[float, ...] = ()      # TTM op margins, newest last
    # prior-year comparatives (Piotroski / trend) — not in the plan's §4 sketch
    net_income_ttm_1y_ago: float | None = None
    cfo_ttm_1y_ago: float | None = None
    gross_margin_ttm_1y_ago: float | None = None
    operating_margin_ttm_1y_ago: float | None = None
    assets: float | None = None
    assets_1y_ago: float | None = None
    current_ratio: float | None = None
    current_ratio_1y_ago: float | None = None
    lt_debt_to_assets: float | None = None
    lt_debt_to_assets_1y_ago: float | None = None
    fcf_margin_avg: float | None = None            # mean (fcf-sbc)/revenue over history
    history: tuple[HistoryPoint, ...] = ()         # oldest first
    unsupported_reason: str | None = None          # foreign filer / IFRS / non-USD
    data_gaps: tuple[str, ...] = ()
    tag_log: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Reason:
    code: str        # e.g. "MOS_HIGH", "RUNWAY_LT_12M", "PFP_GAP"
    points: float    # contribution to valuation score (+/-); 0 = informational
    text: str        # human-readable; shown in trace and UI


@dataclass(frozen=True)
class ScenarioValue:
    name: Literal["bear", "base", "bull"]
    prob: float
    per_share: float
    assumptions: dict[str, float]        # g0, fcf_margin, r, g_terminal, multiple...


@dataclass(frozen=True)
class HealthReport:
    net_cash: float | None
    net_debt_to_ebitda: float | None
    interest_coverage: float | None
    runway_months: float | None          # only when FCF < 0
    dilution_yoy: float | None
    piotroski: int | None                # 0-9
    penalty: float                       # points removed from the health component
    veto_long: bool
    reasons: tuple[Reason, ...]


@dataclass(frozen=True)
class ValuationResult:
    status: Status
    label: Label
    archetype: str
    as_of: date
    price: float
    scenarios: tuple[ScenarioValue, ...]
    expected_value: float | None         # sum(prob * per_share)
    margin_of_safety: float | None       # (expected_value - price) / expected_value
    method_values: dict[str, float]      # base-case per-share value by method
    implied_growth: float | None         # reverse DCF g0
    plausible_growth: float | None
    expectations_gap: float | None       # implied - plausible
    priced_for_perfection: bool
    health: HealthReport | None
    score: int                           # 0-100
    confidence: float                    # 0-1
    reasons: tuple[Reason, ...]
    discount_rate: float | None = None
    quarters_available: int = 0
    ticker: str = ""


@dataclass(frozen=True)
class EarningsEvents:
    days_to_earnings: int | None
    next_date: date | None
    estimated: bool                      # True = projected from last year's cadence
    earnings_move_median: float | None   # median |close[d+1]/close[d-1] - 1|
    past_dates: tuple[date, ...] = ()
