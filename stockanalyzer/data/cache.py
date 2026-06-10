"""Tiny on-disk cache so a full multi-timeframe load stays within free-tier limits.

Caches normalized OHLCV frames as parquet keyed by provider+ticker+timeframe.
TTL is per-timeframe (intraday is short-lived; long ranges barely change intraday),
and `load_stale` supports stale-while-error: serve an expired frame rather than
fail when the provider is rate-limited.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
DEFAULT_TTL_SECONDS = 60 * 30

# Per-timeframe freshness (seconds), keyed by Timeframe.value. Intraday short,
# long ranges long. `live_mode` shortens the 1D bucket for the buy-window.
TTL_BY_TIMEFRAME: dict[str, int] = {
    "1D": 60,
    "5D": 60 * 5,
    "1M": 60 * 15,
    "6M": 60 * 60 * 6,
    "YTD": 60 * 60 * 6,
    "1Y": 60 * 60 * 12,
    "5Y": 60 * 60 * 24,
}


def ttl_for(timeframe_value: str, live_mode: bool = False) -> int:
    if live_mode and timeframe_value == "1D":
        return 55
    return TTL_BY_TIMEFRAME.get(timeframe_value, DEFAULT_TTL_SECONDS)


def _key_path(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.parquet"


def load(key: str, ttl: int = DEFAULT_TTL_SECONDS) -> pd.DataFrame | None:
    """Return the cached frame only if fresher than ttl, else None."""
    path = _key_path(key)
    if not path.exists():
        return None
    if (time.time() - path.stat().st_mtime) > ttl:
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def load_stale(key: str) -> pd.DataFrame | None:
    """Return the cached frame regardless of age (for stale-while-error)."""
    path = _key_path(key)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def store(key: str, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(_key_path(key))
    except Exception:
        # parquet engine missing — silently skip caching rather than fail a request
        pass
