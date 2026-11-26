"""SEC EDGAR adapter — the only place the valuation layer touches the network.

Public-domain data (R7): ticker→CIK map, XBRL ``companyfacts`` and the
``submissions`` filing index. SEC's fair-access rules are honoured: a declared
User-Agent, < 10 requests/second, and raw JSON cached on disk (refreshed daily)
so the engine never fetches. Stale cache is served when SEC is unreachable.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

from .. import config as default_cfg

CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "edgar"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_lock = threading.Lock()
_last_call = [0.0]


class EdgarError(RuntimeError):
    """SEC data unavailable (unknown ticker, no XBRL facts, network down + no cache)."""


def _throttle(cfg) -> None:
    with _lock:
        wait = 1.0 / cfg.SEC_MAX_RPS - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _cached_json(name: str, url: str, cfg, ttl_hours: float | None = None) -> dict:
    path = CACHE_DIR / name
    ttl = (ttl_hours if ttl_hours is not None else cfg.CACHE_TTL_HOURS) * 3600
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        _throttle(cfg)
        resp = requests.get(url, headers={"User-Agent": cfg.SEC_USER_AGENT,
                                          "Accept-Encoding": "gzip, deflate"},
                            timeout=20)
        if resp.status_code == 404:
            raise EdgarError(f"SEC has no data at {url}")
        resp.raise_for_status()
        data = resp.json()
    except EdgarError:
        raise
    except Exception as exc:
        if path.exists():                       # stale-while-error
            return json.loads(path.read_text(encoding="utf-8"))
        raise EdgarError(f"SEC unreachable: {exc}") from exc
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)
    return data


def cik_for(ticker: str, cfg=default_cfg) -> str:
    """Zero-padded 10-digit CIK. Share-class dots map to dashes (BRK.B → BRK-B)."""
    table = _cached_json("company_tickers.json", _TICKERS_URL, cfg, ttl_hours=24 * 7)
    want = ticker.upper().replace(".", "-")
    for row in table.values():
        if str(row.get("ticker", "")).upper() == want:
            return str(row["cik_str"]).zfill(10)
    raise EdgarError(f"{ticker} is not in SEC's ticker list (foreign, ETF or delisted?)")


def company_facts(cik: str, cfg=default_cfg) -> dict:
    return _cached_json(f"facts_{cik}.json", _FACTS_URL.format(cik=cik), cfg)


def submissions(cik: str, cfg=default_cfg) -> dict:
    return _cached_json(f"sub_{cik}.json", _SUBMISSIONS_URL.format(cik=cik), cfg)


def fetch_all(ticker: str, cfg=default_cfg) -> tuple[str, dict, dict]:
    """(cik, companyfacts, submissions) for a ticker — cache-first."""
    cik = cik_for(ticker, cfg)
    try:
        facts = company_facts(cik, cfg)
    except EdgarError:
        facts = {"cik": int(cik), "facts": {}}   # filer with no XBRL facts (new / foreign)
    return cik, facts, submissions(cik, cfg)
