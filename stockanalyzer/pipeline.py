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

    Populates the structural facts on ``Quote`` (latest price, today's regular
    close, prior close, session); all the % moves are derived on the Quote itself,
    so this stays the single source every surface reads from.

    The daily frame is the authority for the two closes. During pre/after-hours
    the 1D frame supplies the extended-hours ``price``; the regular close it is
    measured against still comes from the daily series.
    """
    from .data.market_session import (classify, is_intraday, last_regular_close,
                                      session_closes)

    daily = reports.get(Timeframe.M1) or reports.get(Timeframe.M6)
    reg_close = prev_close = None
    daily_last_day = None
    if daily is not None and len(daily.df) >= 2:
        closes = daily.df["close"]
        reg_close = float(closes.iloc[-1])   # today's regular close (prior session's, pre-open)
        prev_close = float(closes.iloc[-2])
        daily_last_day = daily.df.index[-1].date()

    # The daily-bar feed occasionally lags a session behind — a real yfinance
    # quirk where a day's row goes missing entirely (not just "not posted yet":
    # a LATER day can already be present while a day in between is skipped).
    # `iloc[-2]` above is *assumed* to be "yesterday"; when a session is missing
    # that assumption silently spans two trading days and reports a 1-day change
    # roughly double the real one (see algolab/LEARNINGS.md, 2026-07-27). The
    # intraday frame gets every session's bars as they trade, so cross-check
    # against it whenever it knows about a session the daily frame doesn't.
    intraday_daily = reports.get(Timeframe.D5) or reports.get(Timeframe.D1)
    if intraday_daily is not None and is_intraday(intraday_daily.df):
        sessions = session_closes(intraday_daily.df, n=3)  # most-recent-first
        if sessions:
            latest_day = sessions[0][0]
            if daily_last_day is None or latest_day > daily_last_day:
                # The daily frame doesn't have the latest session at all yet.
                if len(sessions) >= 2:
                    reg_close, prev_close = sessions[0][1], sessions[1][1]
                else:
                    reg_close = sessions[0][1]
            elif latest_day == daily_last_day and len(sessions) >= 2 and daily is not None:
                # The daily frame's last row is current — but is the row BEFORE
                # it really the immediately preceding session, or did the feed
                # skip one? Compare against the intraday frame's own record.
                daily_prev_day = daily.df.index[-2].date()
                if sessions[1][0] != daily_prev_day:
                    prev_close = sessions[1][1]

    # Extended-hours path: the latest 1D bar is pre/after-hours.
    intraday = reports.get(Timeframe.D1)
    if intraday is not None and is_intraday(intraday.df):
        sess = classify(intraday.df.index[-1])
        if sess.is_extended:
            price = float(intraday.df["close"].iloc[-1])
            # Fall back to the intraday regular close if the daily frame is missing.
            reg = reg_close if reg_close is not None else last_regular_close(intraday.df)
            if reg is not None:
                return Quote(price, regular_close=reg, prev_close=prev_close,
                             source="derived", session=sess.value)

    # Regular-session path: latest close of the shortest available timeframe.
    price = None
    for tf in Timeframe:
        if tf in reports:
            price = reports[tf].meta.get("last_close")
            break
    if price is None:
        return None
    return Quote(float(price), regular_close=reg_close if reg_close is not None else float(price),
                 prev_close=prev_close, source="derived")


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
