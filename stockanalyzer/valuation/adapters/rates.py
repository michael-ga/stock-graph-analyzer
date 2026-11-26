"""Risk-free rate adapter: FRED 10-year Treasury (DGS10), cached daily.

Passed into the engine as a parameter (R1). Falls back to
``config.RISK_FREE_DEFAULT`` when FRED is unreachable and nothing is cached.
"""
from __future__ import annotations

import csv
import io
import time
from datetime import date
from pathlib import Path

import requests

from .. import config as default_cfg

_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
_CACHE = Path(__file__).resolve().parents[3] / ".cache" / "edgar" / "dgs10.csv"


def _series() -> list[tuple[date, float]]:
    text = None
    if _CACHE.exists() and time.time() - _CACHE.stat().st_mtime < 24 * 3600:
        text = _CACHE.read_text(encoding="utf-8")
    else:
        try:
            resp = requests.get(_URL, timeout=10)
            resp.raise_for_status()
            text = resp.text
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE.write_text(text, encoding="utf-8")
        except Exception:
            if _CACHE.exists():
                text = _CACHE.read_text(encoding="utf-8")
    if not text:
        return []
    out = []
    for row in csv.reader(io.StringIO(text)):
        try:
            out.append((date.fromisoformat(row[0]), float(row[1]) / 100))
        except (ValueError, IndexError):
            continue                          # header / "." holiday rows
    return out


def risk_free(as_of: date | None = None, cfg=default_cfg) -> float:
    """10y yield (fraction) on or before ``as_of``."""
    as_of = as_of or date.today()
    best = None
    for d, v in _series():
        if d <= as_of:
            best = v
    return best if best is not None else cfg.RISK_FREE_DEFAULT
