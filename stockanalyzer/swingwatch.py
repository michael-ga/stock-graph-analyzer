"""Swing radar — tickers tracked quietly for swing-setup alerts.

The list persists to JSON (like the watchlist). The escalation ladder is pure
and testable: a ticker's swing score crossing 60% fires the 1st notice, 70% the
2nd, 80% the 3rd. A notice fires once per level (no spam); dropping back below a
level re-arms it.
"""
from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / ".swingwatch.json"

LEVELS = (60, 70, 80)
_ORDINAL = {60: "1st", 70: "2nd", 80: "3rd"}


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


def is_tracked(ticker: str, path: Path = _PATH) -> bool:
    return ticker.strip().upper() in _read(path)


# --------------------------------------------------------------------------- #
# Escalation ladder (pure).
# --------------------------------------------------------------------------- #
def notice_level(score: int | float) -> int:
    """Highest alert level the score has reached (0 if below the first rung)."""
    return max((lv for lv in LEVELS if score >= lv), default=0)


def new_notice(prev_level: int, score: int | float) -> tuple[int, str] | None:
    """(new_level, label) when the score climbs onto a higher rung, else None.

    prev_level is the last level already notified for this ticker (0 = none).
    Dropping below a rung simply lowers the stored level — the next climb
    re-fires it.
    """
    level = notice_level(score)
    if level > prev_level:
        return level, f"{_ORDINAL[level]} notice — swing score reached {level}%+"
    return None
