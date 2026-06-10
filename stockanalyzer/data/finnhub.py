"""Finnhub client for fundamentals, analyst data, and news (free tier: 60/min).

Everything degrades gracefully: with no FINNHUB_KEY the client is "unavailable"
and callers fall back to technicals-only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import requests

from . import cache

_BASE = "https://finnhub.io/api/v1"


@dataclass
class Fundamentals:
    name: str = ""
    market_cap: float | None = None      # in millions (Finnhub unit)
    pe: float | None = None
    eps: float | None = None
    beta: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    dividend_yield: float | None = None


@dataclass
class AnalystView:
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    period: str = ""
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell


@dataclass
class NewsItem:
    headline: str
    summary: str
    url: str
    datetime: int            # unix seconds
    source: str = ""
    sentiment: float | None = None   # filled in Phase 4


@dataclass
class Quote:
    price: float
    change: float = 0.0
    change_pct: float = 0.0
    prev_close: float | None = None
    source: str = ""
    session: str = "regular"   # regular / pre-market / after-hours


@dataclass
class CompanyInfo:
    ticker: str
    fundamentals: Fundamentals = field(default_factory=Fundamentals)
    analyst: AnalystView | None = None
    news: list[NewsItem] = field(default_factory=list)
    available: bool = True
    error: str | None = None


class FinnhubClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key or os.environ.get("FINNHUB_KEY", "")).strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, **params) -> dict | list | None:
        params["token"] = self.api_key
        try:
            resp = requests.get(f"{_BASE}/{path}", params=params, timeout=20)
            if resp.status_code != 200:
                return None
            return resp.json()
        except requests.RequestException:
            return None

    # --- individual endpoints -------------------------------------------------
    def profile(self, ticker: str) -> dict:
        return self._get("stock/profile2", symbol=ticker) or {}

    def metrics(self, ticker: str) -> dict:
        data = self._get("stock/metric", symbol=ticker, metric="all") or {}
        return data.get("metric", {}) if isinstance(data, dict) else {}

    def recommendations(self, ticker: str) -> list:
        return self._get("stock/recommendation", symbol=ticker) or []

    def price_target(self, ticker: str) -> dict:
        return self._get("stock/price-target", symbol=ticker) or {}

    def quote(self, ticker: str) -> Quote | None:
        if not self.available:
            return None
        d = self._get("quote", symbol=ticker.upper())
        if not d or not d.get("c"):
            return None
        return Quote(
            price=float(d["c"]),
            change=float(d.get("d") or 0.0),
            change_pct=float(d.get("dp") or 0.0),
            prev_close=float(d["pc"]) if d.get("pc") else None,
            source="finnhub",
        )

    def news(self, ticker: str, _from: str, _to: str) -> list:
        return self._get("company-news", symbol=ticker, **{"from": _from, "to": _to}) or []

    def next_earnings(self, ticker: str, lookahead_days: int = 45) -> str | None:
        """Next confirmed earnings date (YYYY-MM-DD) within `lookahead_days`,
        or None. Free-tier endpoint; cached for a day; degrades silently."""
        if not self.available:
            return None
        from datetime import date, timedelta

        ticker = ticker.upper()
        today = date.today()
        ck = f"finnhub_earnings:{ticker}:{today.isoformat()}"
        cached = _load_pickle(ck, ttl=60 * 60 * 24)
        if cached is not None:
            return cached or None              # "" caches a confirmed miss
        d = self._get("calendar/earnings",
                      **{"from": today.isoformat(),
                         "to": (today + timedelta(days=lookahead_days)).isoformat()},
                      symbol=ticker)
        result = ""
        if isinstance(d, dict):
            dates = sorted(e.get("date", "") for e in d.get("earningsCalendar", [])
                           if e.get("date"))
            if dates:
                result = dates[0]
        _store_pickle(ck, result)
        return result or None

    # --- aggregate ------------------------------------------------------------
    def company_info(self, ticker: str, news_from: str, news_to: str) -> CompanyInfo:
        ticker = ticker.upper()
        if not self.available:
            return CompanyInfo(ticker, available=False, error="FINNHUB_KEY not set")

        ck = f"finnhub_info:{ticker}:{news_from}:{news_to}"
        # company_info isn't a DataFrame, so we cache it separately as JSON-ish via pickle.
        cached = _load_pickle(ck)
        if cached is not None:
            return cached

        prof = self.profile(ticker)
        met = self.metrics(ticker)
        recs = self.recommendations(ticker)
        pt = self.price_target(ticker)
        raw_news = self.news(ticker, news_from, news_to)

        fundamentals = Fundamentals(
            name=prof.get("name", ""),
            market_cap=prof.get("marketCapitalization"),
            pe=met.get("peTTM") or met.get("peBasicExclExtraTTM"),
            eps=met.get("epsTTM") or met.get("epsBasicExclExtraItemsTTM"),
            beta=met.get("beta"),
            high_52w=met.get("52WeekHigh"),
            low_52w=met.get("52WeekLow"),
            dividend_yield=met.get("dividendYieldIndicatedAnnual"),
        )

        analyst = None
        if recs:
            latest = recs[0]  # Finnhub returns newest first
            analyst = AnalystView(
                strong_buy=latest.get("strongBuy", 0),
                buy=latest.get("buy", 0),
                hold=latest.get("hold", 0),
                sell=latest.get("sell", 0),
                strong_sell=latest.get("strongSell", 0),
                period=latest.get("period", ""),
                target_mean=pt.get("targetMean"),
                target_high=pt.get("targetHigh"),
                target_low=pt.get("targetLow"),
            )

        news_items = [
            NewsItem(
                headline=n.get("headline", ""),
                summary=n.get("summary", ""),
                url=n.get("url", ""),
                datetime=int(n.get("datetime", 0)),
                source=n.get("source", ""),
            )
            for n in (raw_news or [])
            if n.get("headline")
        ]
        news_items.sort(key=lambda x: x.datetime, reverse=True)

        info = CompanyInfo(ticker, fundamentals, analyst, news_items[:20], available=True)
        _store_pickle(ck, info)
        return info


# --- tiny pickle cache for non-DataFrame objects -----------------------------
import pickle  # noqa: E402
import time  # noqa: E402


def _pickle_path(key: str):
    import hashlib

    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return cache.CACHE_DIR / f"{digest}.pkl"


def _load_pickle(key: str, ttl: int = 60 * 30):
    path = _pickle_path(key)
    if not path.exists() or (time.time() - path.stat().st_mtime) > ttl:
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _store_pickle(key: str, obj) -> None:
    cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(_pickle_path(key), "wb") as f:
            pickle.dump(obj, f)
    except Exception:
        pass
