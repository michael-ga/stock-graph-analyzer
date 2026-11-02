"""Raw XBRL ``companyfacts`` → a point-in-time ``FundamentalSnapshot`` (plan §3).

Pure (R1): takes the already-fetched JSON and an as-of date. The first thing it
does is drop every fact filed after ``as_of`` (R2), so a backtest at date X sees
exactly what was public at X. Restatements resolve naturally: for each period the
most recent filing *on or before* ``as_of`` wins. The ``frame`` field is never
used — it reflects the latest (possibly restated) value.

The usual XBRL traps handled here (§3.3): YTD cash flows in 10-Qs are differenced
into discrete quarters; Q4 is derived as FY − 9M; periods are keyed by their own
start/end dates (``fy``/``fp`` belong to the filing, not the fact); TTM needs four
consecutive discrete quarters or an annual fact ending on the same date — never 3.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from . import config as default_cfg
from .models import FundamentalSnapshot, HistoryPoint

_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-KT/A"}
_FOREIGN_FORMS = {"20-F", "40-F", "20-F/A", "40-F/A", "6-K"}
_Q = (80, 100)          # a discrete quarter's duration in days
_Y = (350, 380)         # a fiscal year's duration in days

# Field → tag fallbacks in priority order (§3.2). Flows are merged across tags
# (companies switch tags over the years); instants take the first tag present.
FLOW_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating",
                         "InterestExpenseDebt"],
}
INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "st_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                       "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "shares_bs": ["CommonStockSharesOutstanding"],
}
_DEBT_TOTAL = ["LongTermDebt"]                        # already includes current portion
_DEBT_SPLIT = (["LongTermDebtNoncurrent"], ["LongTermDebtCurrent", "DebtCurrent"])
_DEBT_SHORT = ["ShortTermBorrowings", "CommercialPaper"]


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _near(d1: date, d2: date, tol: int) -> bool:
    return abs((d1 - d2).days) <= tol


# --------------------------------------------------------------------------- #
# Fact extraction (point-in-time)
# --------------------------------------------------------------------------- #
class _Facts:
    def __init__(self, raw: dict, as_of: date):
        self.as_of = as_of
        facts = raw.get("facts", {}) or {}
        self.gaap = facts.get("us-gaap", {}) or {}
        self.dei = facts.get("dei", {}) or {}
        self.has_ifrs = bool(facts.get("ifrs-full"))

    def rows(self, tag: str, unit: str = "USD", ns: str = "us-gaap") -> list[dict]:
        src = self.gaap if ns == "us-gaap" else self.dei
        units = (src.get(tag) or {}).get("units", {})
        out = []
        for r in units.get(unit, []):
            filed = r.get("filed")
            if not filed or _d(filed) > self.as_of:          # R2: public by as_of only
                continue
            if ns == "us-gaap" and r.get("form") not in _FORMS:
                continue
            out.append(r)
        return out

    def usd_units(self, tag: str) -> set[str]:
        return set(((self.gaap.get(tag) or {}).get("units") or {}).keys())

    def durations(self, tag: str, unit: str = "USD") -> dict[tuple[date, date], float]:
        """(start, end) → value from the latest filing ≤ as_of (restatements win)."""
        best: dict[tuple[date, date], tuple[str, float]] = {}
        for r in self.rows(tag, unit):
            if "start" not in r:
                continue
            s, e = _d(r["start"]), _d(r["end"])
            if (e - s).days > _Y[1]:
                continue
            k = (s, e)
            if k not in best or r["filed"] >= best[k][0]:
                best[k] = (r["filed"], float(r["val"]))
        return {k: v for k, (_, v) in best.items()}

    def instants(self, tag: str, unit: str = "USD") -> dict[date, float]:
        best: dict[date, tuple[str, float]] = {}
        for r in self.rows(tag, unit):
            if "start" in r:
                continue
            e = _d(r["end"])
            if e not in best or r["filed"] >= best[e][0]:
                best[e] = (r["filed"], float(r["val"]))
        return {k: v for k, (_, v) in best.items()}


# --------------------------------------------------------------------------- #
# Discrete quarters + TTM
# --------------------------------------------------------------------------- #
def discrete_quarters(durs: dict[tuple[date, date], float]) -> dict[date, float]:
    """end → discrete 3-month value. Direct quarterly facts win; the rest are
    derived by differencing consecutive YTD facts that share a start date
    (6M − 3M = Q2, 9M − 6M = Q3, FY − 9M = Q4)."""
    quarters: dict[date, float] = {}
    by_start: dict[date, list[tuple[date, float]]] = {}
    for (s, e), v in durs.items():
        days = (e - s).days
        if _Q[0] <= days <= _Q[1]:
            quarters[e] = v
        by_start.setdefault(s, []).append((e, v))
    derived: dict[date, float] = {}
    for s, items in by_start.items():
        items.sort()
        for (e1, v1), (e2, v2) in zip(items, items[1:]):
            if _Q[0] <= (e2 - e1).days <= _Q[1] and _Q[0] <= (e1 - s).days:
                derived.setdefault(e2, v2 - v1)
    for e, v in derived.items():
        quarters.setdefault(e, v)
    return quarters


def annuals(durs: dict[tuple[date, date], float]) -> dict[date, float]:
    return {e: v for (s, e), v in durs.items() if _Y[0] <= (e - s).days <= _Y[1]}


class _Flow:
    """A flow field (revenue, CFO…) merged across its fallback tags."""

    def __init__(self, facts: _Facts, tags: list[str]):
        self.quarters: dict[date, float] = {}
        self.annual: dict[date, float] = {}
        self.tag: str | None = None
        series = []
        for tag in tags:
            durs = facts.durations(tag)
            q, a = discrete_quarters(durs), annuals(durs)
            if q or a:
                series.append((max(list(q) + list(a)), tag, q, a))
        if not series:
            return
        # The freshest tag is primary (ties → priority order); older tags only
        # fill periods the primary doesn't cover — e.g. SalesRevenueNet pre-2018.
        latest = max(s[0] for s in series)
        series.sort(key=lambda s: s[0] != latest)
        self.tag = series[0][1]
        for _, _, q, a in series:
            for e, v in q.items():
                self.quarters.setdefault(e, v)
            for e, v in a.items():
                self.annual.setdefault(e, v)

    def ttm(self, end: date | None) -> float | None:
        if end is None:
            return None
        for e, v in self.annual.items():                # a 10-K ending here IS the TTM
            if _near(e, end, 3):
                return v
        ends = sorted(e for e in self.quarters if e <= end + timedelta(days=3))
        if not ends or not _near(ends[-1], end, 3):
            return None
        window = ends[-4:]
        if len(window) < 4:
            return None
        for a, b in zip(window, window[1:]):
            if not (_Q[0] <= (b - a).days <= _Q[1]):
                return None                             # a quarter is missing
        return sum(self.quarters[e] for e in window)


def _instant_at(series: dict[date, float], when: date, tol: int = 7) -> float | None:
    cands = [(abs((e - when).days), v) for e, v in series.items() if _near(e, when, tol)]
    return min(cands)[1] if cands else None


def _first_instant(facts: _Facts, tags: list[str], when: date | None,
                   tol: int = 7) -> tuple[float | None, str | None]:
    if when is None:
        return None, None
    for tag in tags:
        v = _instant_at(facts.instants(tag), when, tol)
        if v is not None:
            return v, tag
    return None, None


def _safe_div(a, b):
    return a / b if (a is not None and b not in (None, 0)) else None


# --------------------------------------------------------------------------- #
def _unsupported(facts: _Facts, submissions: dict | None, as_of: date) -> str | None:
    if facts.has_ifrs and not facts.gaap:
        return "IFRS filer (20-F/40-F) — not supported in v1"
    forms: set[str] = set()
    if submissions:
        recent = (submissions.get("filings") or {}).get("recent") or {}
        for f, d in zip(recent.get("form", []), recent.get("filingDate", [])):
            if d and _d(d) <= as_of:
                forms.add(f)
    if forms and not (forms & _FORMS) and (forms & _FOREIGN_FORMS):
        return "foreign private issuer (files 20-F/40-F, not 10-K/10-Q)"
    for tag in FLOW_TAGS["revenue"]:
        units = facts.usd_units(tag)
        if units and "USD" not in units:
            return f"reports revenue in {sorted(units)[0]}, not USD"
    return None


def _shares_out(facts: _Facts, bs_date: date | None) -> tuple[float | None, str | None]:
    """Cover-page shares (dei), summed across share classes on one filing."""
    per_end: dict[date, tuple[str, str, float]] = {}
    sums: dict[tuple[date, str], float] = {}
    for r in facts.rows("EntityCommonStockSharesOutstanding", "shares", ns="dei"):
        e = _d(r["end"])
        sums[(e, r["accn"])] = sums.get((e, r["accn"]), 0.0) + float(r["val"])
        if e not in per_end or r["filed"] >= per_end[e][0]:
            per_end[e] = (r["filed"], r["accn"], 0.0)
    if per_end:
        e = max(per_end)
        return sums[(e, per_end[e][1])], "dei:EntityCommonStockSharesOutstanding"
    v, tag = _first_instant(facts, INSTANT_TAGS["shares_bs"], bs_date, 10)
    if v is not None:
        return v, tag
    return None, None


def _diluted_series(facts: _Facts) -> dict[tuple[date, date], float]:
    return facts.durations("WeightedAverageNumberOfDilutedSharesOutstanding", "shares")


def _dilution_yoy(dil: dict[tuple[date, date], float]) -> float | None:
    """Weighted diluted shares vs the same-length period a year earlier. Shares
    aren't additive, so no YTD differencing — compare like-for-like durations."""
    if not dil:
        return None
    (s, e), v = max(dil.items(), key=lambda kv: kv[0][1])
    length = (e - s).days
    for (s0, e0), v0 in dil.items():
        if _near(e0, e - timedelta(days=364), 20) and abs((e0 - s0).days - length) <= 15:
            return _safe_div(v - v0, v0)
    return None


def _shares_at(dil: dict[tuple[date, date], float], cover: dict[date, float],
               when: date) -> float | None:
    cands = [(abs((e - when).days), v) for (s, e), v in dil.items() if _near(e, when, 10)]
    if cands:
        return min(cands)[1]
    later = sorted((e, v) for e, v in cover.items() if when <= e <= when + timedelta(days=120))
    return later[0][1] if later else None


def _debt_at(facts: _Facts, when: date | None) -> tuple[float | None, str | None]:
    if when is None:
        return None, None
    short = sum(v for v in (_instant_at(facts.instants(t), when) for t in _DEBT_SHORT)
                if v is not None)
    total, tag = _first_instant(facts, _DEBT_TOTAL, when)
    if total is not None:
        return total + short, tag
    nonc, t1 = _first_instant(facts, _DEBT_SPLIT[0], when)
    cur, t2 = _first_instant(facts, _DEBT_SPLIT[1], when)
    if nonc is not None or cur is not None:
        return (nonc or 0.0) + (cur or 0.0) + short, "+".join(t for t in (t1, t2) if t)
    if short:
        return short, "ShortTermBorrowings"
    return None, None


def _cash_at(facts: _Facts, when: date | None) -> tuple[float | None, str | None]:
    cash, tag = _first_instant(facts, INSTANT_TAGS["cash"], when)
    if cash is None:
        return None, None
    st, st_tag = _first_instant(facts, INSTANT_TAGS["st_investments"], when)
    return cash + (st or 0.0), tag + (f"+{st_tag}" if st_tag else "")


# --------------------------------------------------------------------------- #
def build_snapshot(ticker: str, cik: str, raw_facts: dict, submissions: dict | None,
                   as_of: date, cfg=default_cfg) -> FundamentalSnapshot:
    facts = _Facts(raw_facts, as_of)
    gaps: list[str] = []
    tag_log: dict[str, str] = {}
    sic = str(submissions.get("sic")) if submissions and submissions.get("sic") else None

    flows = {k: _Flow(facts, tags) for k, tags in FLOW_TAGS.items()}
    for k, f in flows.items():
        if f.tag:
            tag_log[k] = f.tag

    base = flows["revenue"] if flows["revenue"].quarters else flows["net_income"]
    q_ends = sorted(base.quarters)
    # Latest period = newest quarter the base series can produce a TTM for.
    last_end = None
    for e in reversed(q_ends):
        if base.ttm(e) is not None:
            last_end = e
            break
    if last_end is None and base.annual:
        last_end = max(base.annual)

    def ttm(key, end=last_end, required=True):
        v = flows[key].ttm(end)
        if v is None and required and end == last_end:
            gaps.append(f"{key}_ttm")
        return v

    def year_ago(end):
        if end is None:
            return None
        cands = [e for e in list(base.quarters) + list(base.annual)
                 if _near(e, end - timedelta(days=364), 20)]
        return min(cands, key=lambda e: abs((e - (end - timedelta(days=364))).days)) \
            if cands else None

    rev = ttm("revenue")
    op = ttm("operating_income")
    ni = ttm("net_income")
    cfo = ttm("cfo")
    capex = ttm("capex")
    sbc = ttm("sbc", required=False)
    da = ttm("da", required=False)
    intexp = ttm("interest_expense", required=False)
    gp = ttm("gross_profit", required=False)
    if gp is None and rev is not None:
        cogs = ttm("cost_of_revenue", required=False)
        gp = rev - cogs if cogs is not None else None
    fcf = cfo - capex if (cfo is not None and capex is not None) else None

    e1 = year_ago(last_end)
    rev_1y = flows["revenue"].ttm(e1)
    e3 = last_end - timedelta(days=3 * 364) if last_end else None
    e3 = min((e for e in list(base.quarters) + list(base.annual) if _near(e, e3, 25)),
             key=lambda e: abs((e - e3).days), default=None) if e3 else None
    rev_3y = flows["revenue"].ttm(e3)
    cagr = ((rev / rev_3y) ** (1 / 3) - 1) if (rev and rev_3y and rev > 0 and rev_3y > 0) \
        else None

    def gm(end):
        r = flows["revenue"].ttm(end)
        g = flows["gross_profit"].ttm(end)
        if g is None and r is not None:
            c = flows["cost_of_revenue"].ttm(end)
            g = r - c if c is not None else None
        return _safe_div(g, r)

    def om(end):
        return _safe_div(flows["operating_income"].ttm(end), flows["revenue"].ttm(end))

    # Balance sheet at the latest Assets date (≈ last period end).
    assets_series = facts.instants("Assets")
    bs_date = max(assets_series) if assets_series else last_end
    cash, cash_tag = _cash_at(facts, bs_date)
    debt, debt_tag = _debt_at(facts, bs_date)
    equity, eq_tag = _first_instant(facts, INSTANT_TAGS["equity"], bs_date)
    assets = assets_series.get(bs_date) if bs_date else None
    ca, _ = _first_instant(facts, INSTANT_TAGS["assets_current"], bs_date)
    cl, _ = _first_instant(facts, INSTANT_TAGS["liabilities_current"], bs_date)
    for k, t in (("cash_st", cash_tag), ("total_debt", debt_tag), ("equity", eq_tag)):
        if t:
            tag_log[k] = t
    if cash is None:
        gaps.append("cash_st")
    if debt is None and bs_date is not None:
        gaps.append("total_debt (untagged — treated as 0)")
        debt = 0.0
    if equity is None:
        gaps.append("equity")

    bs_1y = bs_date - timedelta(days=364) if bs_date else None
    assets_1y = _instant_at(assets_series, bs_1y, 20) if bs_1y else None
    ca_1y, _ = _first_instant(facts, INSTANT_TAGS["assets_current"], bs_1y, 20)
    cl_1y, _ = _first_instant(facts, INSTANT_TAGS["liabilities_current"], bs_1y, 20)
    ltd, _ = _first_instant(facts, _DEBT_TOTAL + _DEBT_SPLIT[0], bs_date)
    ltd_1y, _ = _first_instant(facts, _DEBT_TOTAL + _DEBT_SPLIT[0], bs_1y, 20)

    shares, sh_tag = _shares_out(facts, bs_date)
    if sh_tag:
        tag_log["shares_out"] = sh_tag
    else:
        gaps.append("shares_out")
    dil = _diluted_series(facts)
    cover = {}
    for r in facts.rows("EntityCommonStockSharesOutstanding", "shares", ns="dei"):
        cover[_d(r["end"])] = float(r["val"])

    # History: TTM fundamentals at each past quarter end (multiples vs own history).
    hist: list[HistoryPoint] = []
    horizon = last_end - timedelta(days=int(365 * (cfg.MULTIPLE_HISTORY_YEARS + 1))) \
        if last_end else None
    for e in q_ends:
        if horizon is None or e < horizon or e > last_end:
            continue
        r_e = flows["revenue"].ttm(e)
        op_e = flows["operating_income"].ttm(e)
        da_e = flows["da"].ttm(e)
        cfo_e, cap_e = flows["cfo"].ttm(e), flows["capex"].ttm(e)
        c_e, _ = _cash_at(facts, e)
        d_e, _ = _debt_at(facts, e)
        eq_e, _ = _first_instant(facts, INSTANT_TAGS["equity"], e)
        hist.append(HistoryPoint(
            end=e, revenue=r_e, ebit=op_e,
            ebitda=(op_e + da_e) if (op_e is not None and da_e is not None) else None,
            net_income=flows["net_income"].ttm(e),
            fcf=(cfo_e - cap_e) if (cfo_e is not None and cap_e is not None) else None,
            equity=eq_e,
            net_debt=((d_e or 0.0) - c_e) if c_e is not None else None,
            shares=_shares_at(dil, cover, e)))

    fcf_hist = tuple(h.fcf for h in hist[-4:] if h.fcf is not None)
    yearly = hist[::-1][::4][::-1]                       # one point per year, newest last
    op_hist = tuple(h.ebit / h.revenue for h in yearly
                    if h.ebit is not None and h.revenue)
    margins = []
    for h in yearly:
        if h.fcf is not None and h.revenue:
            s = flows["sbc"].ttm(h.end) or 0.0
            margins.append((h.fcf - (s if cfg.SBC_AS_EXPENSE else 0.0)) / h.revenue)

    return FundamentalSnapshot(
        ticker=ticker.upper(), cik=cik, as_of=as_of, last_period_end=last_end,
        quarters_available=len([e for e in q_ends if last_end is None or e <= last_end]),
        sic=sic, revenue_ttm=rev, revenue_ttm_1y_ago=rev_1y, revenue_cagr_3y=cagr,
        gross_margin_ttm=_safe_div(gp, rev), operating_margin_ttm=_safe_div(op, rev),
        ebit_ttm=op, ebitda_ttm=(op + da) if (op is not None and da is not None) else None,
        net_income_ttm=ni, cfo_ttm=cfo, capex_ttm=capex, fcf_ttm=fcf, sbc_ttm=sbc,
        interest_expense_ttm=intexp, cash_st=cash, total_debt=debt, equity=equity,
        shares_out=shares, diluted_shares_yoy=_dilution_yoy(dil),
        fcf_ttm_history=fcf_hist, op_margin_history=op_hist,
        net_income_ttm_1y_ago=flows["net_income"].ttm(e1),
        cfo_ttm_1y_ago=flows["cfo"].ttm(e1),
        gross_margin_ttm_1y_ago=gm(e1), operating_margin_ttm_1y_ago=om(e1),
        assets=assets, assets_1y_ago=assets_1y,
        current_ratio=_safe_div(ca, cl), current_ratio_1y_ago=_safe_div(ca_1y, cl_1y),
        lt_debt_to_assets=_safe_div(ltd, assets),
        lt_debt_to_assets_1y_ago=_safe_div(ltd_1y, assets_1y),
        fcf_margin_avg=mean(margins) if margins else None,
        history=tuple(hist),
        unsupported_reason=_unsupported(facts, submissions, as_of),
        data_gaps=tuple(dict.fromkeys(gaps)), tag_log=tag_log,
    )
