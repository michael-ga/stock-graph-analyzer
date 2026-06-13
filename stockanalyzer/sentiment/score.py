"""Sentiment scoring from analyst ratings, price target, and news.

Phase 2: analyst recommendations + price-target gap (rule-based, explainable).
Phase 4: news headlines scored with VADER and blended in (see ``news_sentiment``).

Returns a score in -1..+1 alongside the reasons, so the verdict can explain it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..data.finnhub import AnalystView, CompanyInfo

# Weights for the five analyst buckets.
_ANALYST_WEIGHTS = {
    "strong_buy": 1.0, "buy": 0.5, "hold": 0.0, "sell": -0.5, "strong_sell": -1.0,
}


@dataclass
class SentimentResult:
    score: float = 0.0                     # -1..+1
    analyst_score: float | None = None
    target_score: float | None = None
    news_score: float | None = None
    reasons: list[str] = field(default_factory=list)
    available: bool = False


def analyst_sentiment(av: AnalystView) -> tuple[float, str] | None:
    if av is None or av.total == 0:
        return None
    raw = (
        _ANALYST_WEIGHTS["strong_buy"] * av.strong_buy
        + _ANALYST_WEIGHTS["buy"] * av.buy
        + _ANALYST_WEIGHTS["sell"] * av.sell
        + _ANALYST_WEIGHTS["strong_sell"] * av.strong_sell
    ) / av.total
    reason = (
        f"Analysts ({av.period}): {av.strong_buy} strong-buy, {av.buy} buy, "
        f"{av.hold} hold, {av.sell} sell, {av.strong_sell} strong-sell "
        f"→ {raw:+.2f}."
    )
    return max(-1.0, min(1.0, raw)), reason


def target_sentiment(av: AnalystView, last_price: float | None) -> tuple[float, str] | None:
    if av is None or av.target_mean is None or not last_price:
        return None
    gap = (av.target_mean - last_price) / last_price
    # Map +-25% upside/downside to +-1, clipped.
    score = max(-1.0, min(1.0, gap / 0.25))
    reason = (
        f"Mean price target {av.target_mean:.2f} vs price {last_price:.2f} "
        f"({gap:+.1%}) → {score:+.2f}."
    )
    return score, reason


# Corporate suffixes stripped before matching, so "Intel Corp" → "intel",
# "Nokia Oyj" → "nokia". Without this the news feed (Finnhub/Yahoo both tag any
# roundup article that merely *mentions* a ticker) drowns the one real story in
# a dozen "5 stocks to watch" listicles — the INTC contamination we hit live.
_NAME_SUFFIXES = re.compile(
    r"\b(corp|corporation|inc|incorporated|co|company|ltd|limited|plc|oyj|ag|"
    r"nv|sa|se|spa|holdings?|group|technologies|technology|systems|"
    r"international|industries|the)\b\.?", re.I)


def _company_aliases(ticker: str | None, name: str | None) -> set[str]:
    """Keywords that mark a headline as genuinely about this company: the
    ticker plus the company name with corporate suffixes stripped (and its
    leading token, e.g. 'intel' from 'Intel Corp')."""
    keys: set[str] = set()
    t = (ticker or "").strip().lower()
    if len(t) >= 2:
        keys.add(t)
    if name:
        cleaned = _NAME_SUFFIXES.sub("", name).strip(" .,&").lower()
        if len(cleaned) >= 2:
            keys.add(cleaned)
            first = cleaned.split()[0] if cleaned.split() else ""
            if len(first) >= 3:
                keys.add(first)
    return keys


def _names_company(headline: str, aliases: set[str]) -> bool:
    """True if the headline actually names the company (word-boundary match).
    With no aliases to judge by, keep the item (don't over-filter)."""
    if not aliases:
        return True
    h = (headline or "").lower()
    return any(re.search(rf"\b{re.escape(k)}\b", h) for k in aliases)


_VADER = None
_VADER_TRIED = False


def _get_vader():
    """Lazily build (and cache) a VADER analyzer; None if not installed."""
    global _VADER, _VADER_TRIED
    if not _VADER_TRIED:
        _VADER_TRIED = True
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

            _VADER = SentimentIntensityAnalyzer()
        except Exception:
            _VADER = None
    return _VADER


def news_sentiment(info: CompanyInfo, max_items: int = 15,
                   min_relevant: int = 1) -> tuple[float, str] | None:
    """Phase 4: score recent headlines with VADER, recency-weighted (newer items
    count more). Annotates each NewsItem.sentiment for display.

    Only headlines that actually name the company are scored — the raw feed is
    polluted with "5 stocks to watch" roundups that merely mention the ticker,
    which average toward general-market mood instead of company news. Returns
    None if VADER is unavailable, there is no news, or fewer than
    ``min_relevant`` headlines genuinely concern this company.
    """
    analyzer = _get_vader()
    if analyzer is None or not info.news:
        return None

    name = info.fundamentals.name if info.fundamentals else None
    aliases = _company_aliases(info.ticker, name)
    relevant = [n for n in info.news if _names_company(n.headline, aliases)]
    filtered = len(info.news) - len(relevant)
    if len(relevant) < min_relevant:
        return None

    items = relevant[:max_items]
    scored: list[tuple[float, float]] = []
    for i, n in enumerate(items):
        text = (n.headline or "") + ". " + (n.summary or "")
        comp = analyzer.polarity_scores(text)["compound"]   # -1..+1
        n.sentiment = round(comp, 3)
        weight = 1.0 - 0.7 * (i / max(1, len(items) - 1))    # newest≈1.0 → oldest≈0.3
        scored.append((comp, weight))

    wsum = sum(w for _, w in scored)
    score = sum(c * w for c, w in scored) / wsum if wsum else 0.0
    extra = f", {filtered} roundup{'s' if filtered != 1 else ''} filtered" if filtered else ""
    reason = (f"News sentiment over {len(items)} {info.ticker}-specific "
              f"headline{'s' if len(items) != 1 else ''} "
              f"(VADER, recency-weighted{extra}) → {score:+.2f}.")
    return max(-1.0, min(1.0, score)), reason


def score_sentiment(info: CompanyInfo, last_price: float | None = None) -> SentimentResult:
    if info is None or not info.available:
        return SentimentResult(available=False, reasons=["No fundamentals/analyst data (set FINNHUB_KEY)."])

    parts: list[tuple[float, float]] = []   # (score, weight)
    res = SentimentResult(available=True)

    a = analyst_sentiment(info.analyst) if info.analyst else None
    if a:
        res.analyst_score = a[0]
        res.reasons.append(a[1])
        parts.append((a[0], 0.6))

    t = target_sentiment(info.analyst, last_price) if info.analyst else None
    if t:
        res.target_score = t[0]
        res.reasons.append(t[1])
        parts.append((t[0], 0.4))

    n = news_sentiment(info)
    if n:
        res.news_score = n[0]
        res.reasons.append(n[1])
        parts.append((n[0], 0.8))   # news is the heaviest input when present

    if parts:
        wsum = sum(w for _, w in parts)
        res.score = round(sum(s * w for s, w in parts) / wsum, 3)
    else:
        res.reasons.append("No analyst/target/news signals available.")

    return res
