"""High-level glue: fetch every timeframe for a ticker, run the engine, pull
fundamentals/analyst/news, score sentiment, and build an explained verdict.
Used by the dashboard and by scripts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .analysis.engine import TimeframeReport, analyze_timeframe
from .data.finnhub import CompanyInfo, FinnhubClient, Quote
from .data.providers import ProviderError, get_provider
from .data.schema import Timeframe
from .sentiment.score import SentimentResult, score_sentiment
from .verdict.aggregate import Verdict, build_verdict


@dataclass
class AnalysisResult:
    ticker: str
    provider: str
    reports: dict[Timeframe, TimeframeReport]
    verdict: Verdict
    company: CompanyInfo | None = None
    sentiment: SentimentResult | None = None
    quote: Quote | None = None
    errors: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    earnings_date: str | None = None    # next confirmed earnings (YYYY-MM-DD)


def _fallback_quote(reports: dict[Timeframe, TimeframeReport]) -> Quote | None:
    """Derive a quote from data already fetched when no Finnhub key is set.

    Session-aware: if the latest intraday (1D) bar is pre/after-hours, use that
    extended price and measure change vs the prior REGULAR close; otherwise use
    the latest daily close vs the previous daily close.
    """
    from .data.market_session import Session, classify, is_intraday, last_regular_close

    # Extended-hours path off the 1D frame.
    intraday = reports.get(Timeframe.D1)
    if intraday is not None and is_intraday(intraday.df):
        sess = classify(intraday.df.index[-1])
        if sess.is_extended:
            ref = last_regular_close(intraday.df)
            price = float(intraday.df["close"].iloc[-1])
            if ref:
                change = price - ref
                return Quote(price, round(change, 2), round(change / ref * 100, 2),
                             ref, source="derived", session=sess.value)

    price = None
    for tf in Timeframe:
        if tf in reports:
            price = reports[tf].meta.get("last_close")
            break
    if price is None:
        return None

    change = change_pct = 0.0
    prev = None
    daily = reports.get(Timeframe.M1) or reports.get(Timeframe.M6)
    if daily is not None and len(daily.df) >= 2:
        closes = daily.df["close"]
        prev = float(closes.iloc[-2])
        change = float(closes.iloc[-1]) - prev
        change_pct = (change / prev * 100) if prev else 0.0
    return Quote(float(price), round(change, 2), round(change_pct, 2), prev, source="derived")


def analyze_ticker(
    ticker: str,
    timeframes: list[Timeframe] | None = None,
    prefer: str | None = None,
    include_fundamentals: bool = True,
    news_days: int = 14,
    live_mode: bool = False,
) -> AnalysisResult:
    timeframes = timeframes or list(Timeframe)
    provider = get_provider(prefer=prefer)

    reports: dict[Timeframe, TimeframeReport] = {}
    errors: dict[str, str] = {}
    notices: list[str] = []

    # The timeframe fetches are independent network calls (~0.4-1.6s each) and
    # so is the Finnhub enrichment batch — run them all concurrently. Cold-load
    # wall time drops from the SUM of ~14 round trips to roughly the slowest one.
    def _frame_job(tf: Timeframe) -> TimeframeReport:
        df = provider.fetch_cached(ticker, tf, live_mode=live_mode, notices=notices)
        return analyze_timeframe(df)

    def _enrich_job():
        client = FinnhubClient()
        if not client.available:
            return None
        to = datetime.now()
        frm = to - timedelta(days=news_days)
        info = client.company_info(ticker, frm.strftime("%Y-%m-%d"), to.strftime("%Y-%m-%d"))
        return info, client.quote(ticker), client.next_earnings(ticker)

    with ThreadPoolExecutor(max_workers=len(timeframes) + 1) as pool:
        frame_futs = {tf: pool.submit(_frame_job, tf) for tf in timeframes}
        enrich_fut = pool.submit(_enrich_job) if include_fundamentals else None
        for tf, fut in frame_futs.items():
            try:
                reports[tf] = fut.result()
            except (ProviderError, ValueError, Exception) as exc:  # keep going per-timeframe
                errors[tf.value] = str(exc)

        company: CompanyInfo | None = None
        quote: Quote | None = None
        earnings_date: str | None = None
        if enrich_fut is not None:
            try:
                enriched = enrich_fut.result()
                if enriched is not None:
                    company, quote, earnings_date = enriched
            except Exception as exc:  # never let enrichment break the core report
                errors["finnhub"] = str(exc)

    last_price = None
    if reports:
        # Prefer the shortest available timeframe's last close as "current price".
        for tf in timeframes:
            if tf in reports:
                last_price = reports[tf].meta.get("last_close")
                break

    sentiment: SentimentResult | None = None
    if include_fundamentals:
        sentiment = score_sentiment(company, last_price) if company else None

    # Prefer the session-aware derived quote during extended hours, since Finnhub's
    # free quote reflects the regular session only.
    derived = _fallback_quote(reports)
    if quote is None or (derived is not None and derived.session != "regular"):
        quote = derived or quote

    sentiment_score = sentiment.score if (sentiment and sentiment.available) else None
    verdict = build_verdict(reports, sentiment_score=sentiment_score)

    return AnalysisResult(
        ticker.upper(), provider.name, reports, verdict, company, sentiment,
        quote, errors, notices, earnings_date,
    )
