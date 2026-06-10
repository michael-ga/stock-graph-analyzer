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


def _news(headlines: list[str]) -> CompanyInfo:
    items = [NewsItem(headline=h, summary="", url="", datetime=int(time.time()) - i * 3600)
             for i, h in enumerate(headlines)]
    return CompanyInfo("TEST", Fundamentals(), None, items, available=True)


def test_news_sentiment_positive():
    pytest.importorskip("vaderSentiment")
    info = _news([
        "Company smashes earnings, raises guidance, stock soars to record high",
        "Analysts thrilled with strong growth and excellent outlook",
    ])
    out = news_sentiment(info)
    assert out is not None
    assert out[0] > 0.2
    assert info.news[0].sentiment is not None  # annotated for display


def test_news_sentiment_negative():
    pytest.importorskip("vaderSentiment")
    info = _news([
        "Company plunges on disastrous earnings miss and weak guidance",
        "Investors panic as fraud probe and massive losses mount",
    ])
    out = news_sentiment(info)
    assert out is not None
    assert out[0] < -0.2
