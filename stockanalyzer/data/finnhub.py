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
    """The single source of truth for a ticker's price and change.

    Every price surface (main header, live header, swing radar, day card) reads
    from one of these, and every displayed delta is *derived* here rather than
    recomputed per surface — so the numbers can't drift apart. Callers set only
    the three structural facts; the moves are properties:

    - ``price``          — the latest print: the extended-hours price during
                           pre/after-hours, otherwise the regular-session price.
    - ``regular_close``  — today's regular-session close (the "day" price). During
                           pre-market this is the prior session's close, which is
                           what a pre-market move is measured against.
    - ``prev_close``     — the close of the trading day *before* ``regular_close``;
                           the base for the day's percentage move.

    The day move (``day_change`` / ``day_change_pct``) is regular_close vs
    prev_close — the headline number every surface leads with. The extended move
    (``ext_change`` / ``ext_change_pct``) is price vs regular_close, shown only as
    a secondary line during pre/after-hours.
    """
    price: float
    regular_close: float | None = None
    prev_close: float | None = None
    source: str = ""
    session: str = "regular"   # regular / pre-market / after-hours

    @property
    def is_extended(self) -> bool:
        return self.session in ("pre-market", "after-hours")

    @property
    def day_change(self) -> float:
        if self.regular_close is None or self.prev_close is None:
            return 0.0
        return round(self.regular_close - self.prev_close, 2)

    @property
    def day_change_pct(self) -> float:
        if not self.prev_close or self.regular_close is None:
            return 0.0
        return round((self.regular_close - self.prev_close) / self.prev_close * 100, 2)

    @property
    def ext_change(self) -> float:
        if not self.is_extended or self.regular_close is None:
            return 0.0
        return round(self.price - self.regular_close, 2)

    @property
    def ext_change_pct(self) -> float:
        if not self.is_extended or not self.regular_close:
            return 0.0
        return round((self.price - self.regular_close) / self.regular_close * 100, 2)

    def pct_from_prev(self, price: float | None = None) -> float:
        """Percent move of ``price`` (default: the latest print) vs prev_close.

        The one formula surfaces use for a live 'today vs yesterday' read, so a
        header showing the streaming tick and the radar showing the last print
        agree by construction.
        """
        p = self.price if price is None else price
        if not self.prev_close or p is None:
            return 0.0
        return round((p - self.prev_close) / self.prev_close * 100, 2)

    # Back-compat: the headline change is the day move.
    @property
    def change(self) -> float:
        return self.day_change

    @property
    def change_pct(self) -> float:
        return self.day_change_pct


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
        # Finnhub's free quote is regular-session only: `c` is the current/last
        # regular price, `pc` the prior close. day_change then equals Finnhub's d/dp.
        return Quote(
            price=float(d["c"]),
            regular_close=float(d["c"]),
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

        # Five independent endpoints — fetch concurrently (each _get already
        # degrades to None/{}/[] on failure, so a slow/failed one can't poison
        # the batch).
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=5) as pool:
            f_prof = pool.submit(self.profile, ticker)
            f_met = pool.submit(self.metrics, ticker)
            f_recs = pool.submit(self.recommendations, ticker)
            f_pt = pool.submit(self.price_target, ticker)
            f_news = pool.submit(self.news, ticker, news_from, news_to)
        prof, met, recs = f_prof.result(), f_met.result(), f_recs.result()
        pt, raw_news = f_pt.result(), f_news.result()

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
