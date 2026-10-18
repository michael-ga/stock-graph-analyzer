"""Database-backed OHLCV cache with stale-while-error semantics."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

from stockanalyzer.db.repositories.ohlcv import OhlcvRepository

# Kept as a compatibility symbol only; normal operation never writes here.
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
DEFAULT_TTL_SECONDS = 60 * 30
TTL_BY_TIMEFRAME = {
    "1D": 60, "5D": 300, "1M": 900, "6M": 21600,
    "YTD": 21600, "1Y": 43200, "5Y": 86400,
}
_repo = OhlcvRepository()
_memory: dict[str, tuple[float, pd.DataFrame]] = {}


def ttl_for(timeframe_value: str, live_mode: bool = False) -> int:
    if live_mode and timeframe_value == "1D":
        return 55
    return TTL_BY_TIMEFRAME.get(timeframe_value, DEFAULT_TTL_SECONDS)


def load(key: str, ttl: int = DEFAULT_TTL_SECONDS) -> pd.DataFrame | None:
    if os.getenv("DATABASE_URL"):
        return _repo.load(key, ttl)
    item = _memory.get(key)
    return item[1].copy() if item and time.time() - item[0] <= ttl else None


def load_stale(key: str) -> pd.DataFrame | None:
    if os.getenv("DATABASE_URL"):
        return _repo.load_stale(key)
    item = _memory.get(key)
    return item[1].copy() if item else None


def store(key: str, df: pd.DataFrame) -> None:
    if os.getenv("DATABASE_URL"):
        _repo.store(key, df)
    else:
        _memory[key] = (time.time(), df.copy())
