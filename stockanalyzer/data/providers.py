"""Data providers. yfinance is the keyless default; Twelve Data is the keyed
'primary, exact' source from the plan when TWELVEDATA_KEY is set.

All providers return the canonical OHLCV contract via ``validate_ohlcv``.
"""
from __future__ import annotations

import os

import pandas as pd

from . import cache
from .ratelimit import LIMITER, RateLimitExceeded
from .schema import TIMEFRAME_SPECS, Timeframe, validate_ohlcv


class ProviderError(RuntimeError):
    """Raised when a provider cannot return usable data."""


class BaseProvider:
    name = "base"

    def fetch(self, ticker: str, timeframe: Timeframe) -> pd.DataFrame:  # pragma: no cover
        raise NotImplementedError

    def fetch_cached(
        self,
        ticker: str,
        timeframe: Timeframe,
        live_mode: bool = False,
        notices: list[str] | None = None,
    ) -> pd.DataFrame:
        """Cache-first fetch with rate-limiting and stale-while-error.

        1. Fresh cache hit (per-timeframe TTL) → return it.
        2. Otherwise acquire a rate-limit token and fetch; cache and return.
        3. If rate-limited or the fetch fails, serve stale cache (with a notice)
           rather than erroring; only raise if there is no cache at all.
        """
        key = f"{self.name}:{ticker.upper()}:{timeframe.value}"
        ttl = cache.ttl_for(timeframe.value, live_mode=live_mode)

        fresh = cache.load(key, ttl)
        if fresh is not None:
            return fresh

        try:
            LIMITER.acquire(self.name)
            df = validate_ohlcv(self.fetch(ticker, timeframe))
            cache.store(key, df)
            return df
        except (RateLimitExceeded, ProviderError, ValueError, Exception) as exc:
            stale = cache.load_stale(key)
            if stale is not None:
                if notices is not None:
                    notices.append(f"{timeframe.value}: showing cached data ({exc})")
                return stale
            raise


class YFinanceProvider(BaseProvider):
    """Keyless, good for instant use. Scraping-based, so treated as a fallback."""

    name = "yfinance"

    def fetch(self, ticker: str, timeframe: Timeframe) -> pd.DataFrame:
        import yfinance as yf

        spec = TIMEFRAME_SPECS[timeframe]
        # Include pre-market & after-hours for intraday intervals (1D/5D). yfinance
        # is the only free source of extended-hours bars (they come back with
        # volume=0 and timestamps outside 09:30–16:00 ET).
        intraday = any(u in spec.interval for u in ("m", "h"))
        df = yf.download(
            ticker,
            period=spec.period,
            interval=spec.interval,
            auto_adjust=False,
            progress=False,
            threads=False,
            prepost=intraday,
        )
        if df is None or len(df) == 0:
            raise ProviderError(f"yfinance returned no data for {ticker} {timeframe.value}")
        # yfinance may return MultiIndex columns for a single ticker; flatten.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        return df


class TwelveDataProvider(BaseProvider):
    """Exact OHLCV across all timeframes. Free tier: 800 calls/day."""

    name = "twelvedata"

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("TwelveDataProvider requires an API key")
        self.api_key = api_key

    def fetch(self, ticker: str, timeframe: Timeframe) -> pd.DataFrame:
        from datetime import date

        import requests

        spec = TIMEFRAME_SPECS[timeframe]
        outputsize = spec.td_outputsize
        if timeframe is Timeframe.YTD:
            # Trading days since Jan 1 ≈ calendar days × 5/7, capped to Twelve Data's max.
            days = (date.today() - date(date.today().year, 1, 1)).days
            outputsize = max(5, min(5000, int(days * 5 / 7) + 2))

        intraday = any(u in spec.td_interval for u in ("min", "h"))
        params = {
            "symbol": ticker.upper(),
            "interval": spec.td_interval,
            "outputsize": outputsize,
            "apikey": self.api_key,
            "order": "ASC",
        }
        # Extended hours for intraday. NOTE: Twelve Data only returns pre/post data
        # on PAID tiers — on the free tier this is ignored (use yfinance for free
        # extended-hours data).
        if intraday:
            params["prepost"] = "true"

        resp = requests.get(
            "https://api.twelvedata.com/time_series",
            params=params,
            timeout=20,
        )
        data = resp.json()
        if data.get("status") == "error" or "values" not in data:
            raise ProviderError(f"Twelve Data: {data.get('message', 'unknown error')}")
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime")
        return df


def get_provider(prefer: str | None = None) -> BaseProvider:
    """Pick a provider. Twelve Data if its key is set (or explicitly preferred),
    otherwise yfinance. ``prefer`` can force 'yfinance' or 'twelvedata'.

    If Twelve Data is requested/implied but no key is set, fall back to yfinance
    (the keyless default) rather than erroring — callers can compare
    ``provider.name`` to the request to notice the fallback.
    """
    key = os.environ.get("TWELVEDATA_KEY", "").strip()
    if prefer == "yfinance":
        return YFinanceProvider()
    if (prefer == "twelvedata" or prefer is None) and key:
        return TwelveDataProvider(key)
    return YFinanceProvider()
