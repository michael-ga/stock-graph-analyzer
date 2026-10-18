"""Per-user followed tickers stored in PostgreSQL during normal operation.

An explicit ``Path`` is retained only as a compatibility adapter for legacy-data
migration tests; the production UI always supplies ``user_id`` and never writes JSON.
"""
from __future__ import annotations
import json
from pathlib import Path
from stockanalyzer.db.repositories.watchlist import WatchlistRepository

_PATH = Path(__file__).resolve().parent.parent / ".watchlist.json"
_repo = WatchlistRepository()


def _legacy_read(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
        return [str(t).upper() for t in data] if isinstance(data, list) else []
    except Exception:
        return []


def _legacy_write(path: Path, tickers: list[str]) -> None:
    path.write_text(json.dumps(tickers))


def load(path: Path | None = None, *, user_id: str | None = None) -> list[str]:
    if path is not None:
        return _legacy_read(path)
    if not user_id:
        raise ValueError("user_id is required")
    return _repo.list(user_id)


def add(ticker: str, path: Path | None = None, *, user_id: str | None = None) -> list[str]:
    if path is not None:
        ticker = ticker.strip().upper(); items = _legacy_read(path)
        if ticker and ticker not in items: items.append(ticker); _legacy_write(path, items)
        return items
    if not user_id: raise ValueError("user_id is required")
    return _repo.add(user_id, ticker)


def remove(ticker: str, path: Path | None = None, *, user_id: str | None = None) -> list[str]:
    if path is not None:
        items = [t for t in _legacy_read(path) if t != ticker.strip().upper()]
        _legacy_write(path, items); return items
    if not user_id: raise ValueError("user_id is required")
    return _repo.remove(user_id, ticker)


def toggle(ticker: str, path: Path | None = None, *, user_id: str | None = None) -> list[str]:
    if path is not None:
        return remove(ticker, path) if ticker.strip().upper() in _legacy_read(path) else add(ticker, path)
    if not user_id: raise ValueError("user_id is required")
    return _repo.toggle(user_id, ticker)


def is_followed(ticker: str, path: Path | None = None, *, user_id: str | None = None) -> bool:
    if path is not None: return ticker.strip().upper() in _legacy_read(path)
    if not user_id: raise ValueError("user_id is required")
    return _repo.contains(user_id, ticker)
