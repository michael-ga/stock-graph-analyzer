"""Multiples vs the company's own history (plan §6.5, v1).

At each past quarter end the TTM metric is paired with the price ~45 days later
(when those numbers were actually public) — never later than the snapshot's
as-of date. Share counts from old filings are pre-split, while the price feed is
split-adjusted, so historical shares are scaled by every split after that date.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta

from . import config as default_cfg
from .models import FundamentalSnapshot, HistoryPoint

PRIMARY = {"MATURE": "ev_ebit", "GROWTH_PROFITABLE": "ev_sales",
           "GROWTH_UNPROFITABLE": "ev_sales", "CYCLICAL": "ev_ebitda",
           "FINANCIAL": "pb", "FALLBACK": "ev_sales"}
LABEL = {"pe": "P/E", "ev_sales": "EV/Sales", "ev_ebit": "EV/EBIT",
         "ev_ebitda": "EV/EBITDA", "pb": "P/B"}


@dataclass(frozen=True)
class MultipleRead:
    kind: str
    current: float
    series: tuple[float, ...]
    percentile: float | None          # 0-100 rank of current in the series
    p25: float | None
    median: float | None
    p75: float | None


def _metric(kind: str, h) -> float | None:
    m = {"pe": h.net_income, "ev_sales": h.revenue, "ev_ebit": h.ebit,
         "ev_ebitda": h.ebitda, "pb": h.equity}[kind]
    return m if (m is not None and m > 0) else None


def _value(kind: str, mcap: float, net_debt: float | None) -> float | None:
    if kind in ("pe", "pb"):
        return mcap
    return mcap + net_debt if net_debt is not None else None


def price_on(prices: tuple[tuple[date, float], ...], when: date) -> float | None:
    """Last close on or before ``when`` (prices sorted ascending)."""
    i = bisect_right([d for d, _ in prices], when)
    return prices[i - 1][1] if i else None


def _split_factor(splits: tuple[tuple[date, float], ...], after: date, upto: date) -> float:
    f = 1.0
    for d, ratio in splits:
        if after < d <= upto and ratio > 0:
            f *= ratio
    return f


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def read(s: FundamentalSnapshot, price: float, kind: str,
         prices: tuple[tuple[date, float], ...], splits: tuple[tuple[date, float], ...] = (),
         cfg=default_cfg) -> MultipleRead | None:
    net_debt = ((s.total_debt or 0.0) - s.cash_st) if s.cash_st is not None else None
    now = HistoryPoint(end=s.as_of, revenue=s.revenue_ttm, ebit=s.ebit_ttm,
                       ebitda=s.ebitda_ttm, net_income=s.net_income_ttm, fcf=s.fcf_ttm,
                       equity=s.equity, net_debt=net_debt, shares=s.shares_out)
    metric = _metric(kind, now)
    if metric is None or not s.shares_out:
        return None
    val = _value(kind, price * s.shares_out, net_debt)
    if val is None or val <= 0:
        return None
    current = val / metric

    cutoff = s.as_of - timedelta(days=int(365 * cfg.MULTIPLE_HISTORY_YEARS))
    series: list[float] = []
    for h in s.history:
        seen = h.end + timedelta(days=cfg.FILING_LAG_DAYS)
        if h.end < cutoff or seen > s.as_of or not h.shares:
            continue
        p = price_on(prices, seen)
        m = _metric(kind, h)
        if p is None or m is None:
            continue
        shares = h.shares * _split_factor(splits, h.end, s.as_of)
        v = _value(kind, p * shares, h.net_debt)
        if v is not None and v > 0:
            series.append(v / m)
    if len(series) < cfg.MULTIPLE_MIN_POINTS:
        return MultipleRead(kind, current, tuple(series), None, None, None, None)
    pct = 100.0 * sum(1 for x in series if x <= current) / len(series)
    return MultipleRead(kind, current, tuple(series), pct, _quantile(series, 0.25),
                        _quantile(series, 0.5), _quantile(series, 0.75))


def implied_per_share(s: FundamentalSnapshot, kind: str, multiple: float) -> float | None:
    """Per-share value if the stock traded at ``multiple`` on today's metric."""
    net_debt = ((s.total_debt or 0.0) - s.cash_st) if s.cash_st is not None else None
    metric = _metric(kind, HistoryPoint(s.as_of, s.revenue_ttm, s.ebit_ttm, s.ebitda_ttm,
                                        s.net_income_ttm, s.fcf_ttm, s.equity, net_debt,
                                        s.shares_out))
    if metric is None or not s.shares_out:
        return None
    total = multiple * metric
    if kind not in ("pe", "pb"):
        if net_debt is None:
            return None
        total -= net_debt
    return max(total, 0.0) / s.shares_out
