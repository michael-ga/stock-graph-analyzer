"""Can the company survive being wrong? (plan §6.6)

Runway, dilution, leverage, coverage and the Piotroski F-score. The one hard
output is ``veto_long``: a cash-burning company with under 6 months of runway
that is *also* diluting ≥10%/yr is the textbook dilution trap — valuation may
block a long there even when the chart looks fine (R4).
"""
from __future__ import annotations

from . import config as default_cfg
from .models import FundamentalSnapshot, HealthReport, Reason


def _div(a, b):
    return a / b if (a is not None and b not in (None, 0)) else None


def piotroski(s: FundamentalSnapshot) -> tuple[int | None, list[str]]:
    """(0-9 score, passed checks). None when more than 2 inputs are missing."""
    roa, roa_1y = _div(s.net_income_ttm, s.assets), _div(s.net_income_ttm_1y_ago, s.assets_1y_ago)
    turn = _div(s.revenue_ttm, s.assets)
    turn_1y = _div(s.revenue_ttm_1y_ago, s.assets_1y_ago)
    checks = {
        "ROA > 0": None if roa is None else roa > 0,
        "CFO > 0": None if s.cfo_ttm is None else s.cfo_ttm > 0,
        "ROA improving": None if (roa is None or roa_1y is None) else roa > roa_1y,
        "CFO > net income": None if (s.cfo_ttm is None or s.net_income_ttm is None)
        else s.cfo_ttm > s.net_income_ttm,
        "leverage falling": None if (s.lt_debt_to_assets is None
                                     or s.lt_debt_to_assets_1y_ago is None)
        else s.lt_debt_to_assets <= s.lt_debt_to_assets_1y_ago,
        "current ratio rising": None if (s.current_ratio is None or s.current_ratio_1y_ago is None)
        else s.current_ratio > s.current_ratio_1y_ago,
        "no share issuance": None if s.diluted_shares_yoy is None
        else s.diluted_shares_yoy <= 0.005,
        "gross margin rising": None if (s.gross_margin_ttm is None
                                        or s.gross_margin_ttm_1y_ago is None)
        else s.gross_margin_ttm > s.gross_margin_ttm_1y_ago,
        "asset turnover rising": None if (turn is None or turn_1y is None) else turn > turn_1y,
    }
    if sum(v is None for v in checks.values()) > 2:
        return None, []
    passed = [k for k, v in checks.items() if v]
    return len(passed), passed


def assess(s: FundamentalSnapshot, archetype: str, cfg=default_cfg) -> HealthReport:
    reasons: list[Reason] = []
    penalty = 0.0
    P = cfg.HEALTH_PENALTY
    financial = archetype == "FINANCIAL"

    net_cash = (s.cash_st - s.total_debt) if (s.cash_st is not None
                                             and s.total_debt is not None) else None
    if net_cash is not None and net_cash > 0 and not financial:
        reasons.append(Reason("NET_CASH", 0, "Net cash balance sheet"))

    nd_ebitda = None
    coverage = None
    if not financial:                       # a bank's debt is inventory, not leverage
        if net_cash is not None and s.ebitda_ttm and s.ebitda_ttm > 0:
            nd_ebitda = -net_cash / s.ebitda_ttm
            if nd_ebitda > cfg.LEVERAGE_SEVERE:
                penalty += P["severe"]
                reasons.append(Reason("LEVERAGE_SEVERE", -P["severe"],
                                      f"net debt {nd_ebitda:.1f}× EBITDA"))
            elif nd_ebitda > cfg.LEVERAGE_WARN:
                penalty += P["warn"]
                reasons.append(Reason("LEVERAGE_HIGH", -P["warn"],
                                      f"net debt {nd_ebitda:.1f}× EBITDA"))
        if s.ebit_ttm is not None and s.interest_expense_ttm and s.interest_expense_ttm > 0:
            coverage = s.ebit_ttm / s.interest_expense_ttm
            if coverage < cfg.COVERAGE_SEVERE:
                penalty += P["severe"]
                reasons.append(Reason("COVERAGE_SEVERE", -P["severe"],
                                      f"EBIT covers interest only {coverage:.1f}×"))
            elif coverage < cfg.COVERAGE_WARN:
                penalty += P["warn"]
                reasons.append(Reason("COVERAGE_LOW", -P["warn"],
                                      f"EBIT covers interest only {coverage:.1f}×"))

    runway = None
    runway_severe = False
    if s.fcf_ttm is not None and s.fcf_ttm < 0 and s.cash_st is not None:
        runway = s.cash_st / (-s.fcf_ttm / 12)
        if runway < cfg.RUNWAY_SEVERE_M:
            runway_severe = True
            penalty += P["severe"]
            reasons.append(Reason("RUNWAY_LT_6M", -P["severe"],
                                  f"cash runway ~{runway:.0f} months at the current burn"))
        elif runway < cfg.RUNWAY_WARN_M:
            penalty += P["warn"]
            reasons.append(Reason("RUNWAY_LT_12M", -P["warn"],
                                  f"cash runway ~{runway:.0f} months at the current burn"))
        else:
            reasons.append(Reason("RUNWAY_OK", 0, f"cash runway ~{runway:.0f} months"))

    dil = s.diluted_shares_yoy
    if dil is not None:
        if dil >= cfg.DILUTION_SEVERE:
            penalty += P["severe"]
            reasons.append(Reason("DILUTION_SEVERE", -P["severe"],
                                  f"share count up {dil:.0%} in a year"))
        elif dil > cfg.DILUTION_WARN:
            penalty += P["warn"]
            reasons.append(Reason("DILUTION_HIGH", -P["warn"],
                                  f"share count up {dil:.0%} in a year"))
        elif dil < -0.005:
            reasons.append(Reason("BUYBACKS", 0, f"share count down {-dil:.1%} in a year"))
        else:
            reasons.append(Reason("DILUTION_LOW", 0, f"dilution {dil:.0%}/yr"))

    f_score, passed = piotroski(s)
    if f_score is not None:
        if f_score <= cfg.PIOTROSKI_WEAK:
            penalty += P["piotroski_weak"]
            reasons.append(Reason("PIOTROSKI_WEAK", -P["piotroski_weak"],
                                  f"Piotroski F-score {f_score}/9 — weak fundamentals"))
        elif f_score >= cfg.PIOTROSKI_STRONG:
            reasons.append(Reason("PIOTROSKI_STRONG", 0,
                                  f"Piotroski F-score {f_score}/9 — strong fundamentals"))
        else:
            reasons.append(Reason("PIOTROSKI", 0, f"Piotroski F-score {f_score}/9"))

    veto = runway_severe and dil is not None and dil >= cfg.DILUTION_SEVERE
    if veto:
        reasons.append(Reason("HEALTH_VETO", 0,
                              "under 6 months of cash while diluting ≥10%/yr — "
                              "dilution trap, long blocked"))
    return HealthReport(net_cash=net_cash, net_debt_to_ebitda=nd_ebitda,
                        interest_coverage=coverage, runway_months=runway,
                        dilution_yoy=dil, piotroski=f_score,
                        penalty=min(penalty, 20.0), veto_long=veto, reasons=tuple(reasons))
