"""Phase 2 sentiment tests — use mock Finnhub objects (no network / key needed)."""
from __future__ import annotations

import time

import pytest

from stockanalyzer.data.finnhub import AnalystView, CompanyInfo, Fundamentals, NewsItem
from stockanalyzer.sentiment.score import news_sentiment, score_sentiment


def _info(analyst: AnalystView | None) -> CompanyInfo:
    return CompanyInfo("TEST", Fundamentals(name="Test"), analyst, [], available=True)


def test_unavailable_company_returns_unavailable():
    res = score_sentiment(None, 100.0)
    assert res.available is False
    assert res.score == 0.0


def test_bullish_analyst_consensus_positive():
    av = AnalystView(strong_buy=8, buy=4, hold=2, sell=0, strong_sell=0,
                     period="2026-06-01", target_mean=120, target_high=140, target_low=110)
    res = score_sentiment(_info(av), last_price=100.0)
    assert res.available
    assert res.score > 0.3
    assert res.analyst_score is not None and res.analyst_score > 0
    assert res.target_score is not None and res.target_score > 0  # 20% upside


def test_bearish_analyst_consensus_negative():
    av = AnalystView(strong_buy=0, buy=1, hold=2, sell=5, strong_sell=6,
                     period="2026-06-01", target_mean=80, target_high=95, target_low=70)
    res = score_sentiment(_info(av), last_price=100.0)
    assert res.score < -0.3


def test_no_analyst_data_is_neutral():
    res = score_sentiment(_info(None), last_price=100.0)
    assert res.available
    assert res.score == 0.0


def test_target_gap_clipped():
    av = AnalystView(strong_buy=1, hold=1, period="x",
                     target_mean=1000, target_high=1200, target_low=900)
    res = score_sentiment(_info(av), last_price=100.0)  # +900% upside → clipped to +1
    assert res.target_score == 1.0


def _news(headlines: list[str], ticker: str = "INTC", name: str = "Intel Corp") -> CompanyInfo:
    items = [NewsItem(headline=h, summary="", url="", datetime=int(time.time()) - i * 3600)
             for i, h in enumerate(headlines)]
    return CompanyInfo(ticker, Fundamentals(name=name), None, items, available=True)


def test_news_sentiment_positive():
    pytest.importorskip("vaderSentiment")
    info = _news([
        "Intel smashes earnings, raises guidance, stock soars to record high",
        "Analysts thrilled with Intel's strong growth and excellent outlook",
    ])
    out = news_sentiment(info)
    assert out is not None
    assert out[0] > 0.2
    assert info.news[0].sentiment is not None  # annotated for display


def test_news_sentiment_negative():
    pytest.importorskip("vaderSentiment")
    info = _news([
        "Intel plunges on disastrous earnings miss and weak guidance",
        "Investors panic over Intel as fraud probe and massive losses mount",
    ])
    out = news_sentiment(info)
    assert out is not None
    assert out[0] < -0.2


def test_news_filters_unrelated_roundups():
    """The INTC contamination: a feed of market roundups that merely mention the
    ticker must be dropped so the one real company story drives the score."""
    pytest.importorskip("vaderSentiment")
    info = _news([
        "Space Stocks Got Crushed on SpaceX's Big Day. Is the Sell-Off a Warning?",
        "Why Uranium Energy Stock Plummeted This Week",
        "Plug Power Is Undergoing a Massive Transformation: 3 Things to Know",
        "Intel soars on blockbuster earnings and record profit, beating estimates",
    ])
    out = news_sentiment(info)
    assert out is not None
    # Only the Intel headline is scored → strongly positive, not diluted to ~0.
    assert out[0] > 0.2
    assert "1 INTC-specific headline" in out[1]
    assert "3 roundups filtered" in out[1]
    # The unrelated roundups are never annotated.
    assert info.news[0].sentiment is None
    assert info.news[3].sentiment is not None


def test_news_none_when_no_company_specific_headlines():
    """A feed with zero headlines naming the company yields no news signal at
    all — better than scoring general-market noise."""
    pytest.importorskip("vaderSentiment")
    info = _news([
        "5 Stocks to Watch This Week as Markets Rally",
        "Why the Fed's Next Move Could Rattle Wall Street",
    ])
    assert news_sentiment(info) is None


def test_ticker_alias_matches_without_overmatching():
    """'NOK' must match 'NOK Stock' but a word-boundary guard keeps it from
    firing inside unrelated words."""
    pytest.importorskip("vaderSentiment")
    info = _news(
        ["NOK Stock Climbs as Nokia Wins 5G Deal", "Markets knock back on rate fears"],
        ticker="NOK", name="Nokia Oyj")
    out = news_sentiment(info)
    assert out is not None
    assert "1 NOK-specific headline" in out[1]  # only the first, not "knock"
