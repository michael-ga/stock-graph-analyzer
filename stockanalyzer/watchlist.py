"""Followed tickers, persisted to a JSON file so they survive app restarts."""
from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / ".watchlist.json"


def _read(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return [str(t).upper() for t in data]
    except Exception:
        pass
    return []


def _write(path: Path, tickers: list[str]) -> None:
    try:
        path.write_text(json.dumps(tickers))
    except Exception:
        pass


def load(path: Path = _PATH) -> list[str]:
    return _read(path)


def add(ticker: str, path: Path = _PATH) -> list[str]:
    ticker = ticker.strip().upper()
    tickers = _read(path)
    if ticker and ticker not in tickers:
        tickers.append(ticker)
        _write(path, tickers)
    return tickers


def remove(ticker: str, path: Path = _PATH) -> list[str]:
    ticker = ticker.strip().upper()
    tickers = [t for t in _read(path) if t != ticker]
    _write(path, tickers)
    return tickers


def toggle(ticker: str, path: Path = _PATH) -> list[str]:
    ticker = ticker.strip().upper()
    if ticker in _read(path):
        return remove(ticker, path)
    return add(ticker, path)


def is_followed(ticker: str, path: Path = _PATH) -> bool:
    return ticker.strip().upper() in _read(path)
