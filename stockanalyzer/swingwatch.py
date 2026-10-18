"""Per-user swing radar state stored in PostgreSQL."""
from __future__ import annotations
import json
from pathlib import Path
from stockanalyzer.db.repositories.swingwatch import SwingWatchRepository
from stockanalyzer.auth import AuthService

_PATH = Path(__file__).resolve().parent.parent / ".swingwatch.json"
LEVELS = (60, 70, 80)
_ORDINAL = {60: "1st", 70: "2nd", 80: "3rd"}
_repo = SwingWatchRepository()


def _legacy_read(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
        return [str(t).upper() for t in data] if isinstance(data, list) else []
    except Exception: return []


def load(path: Path | None = None, *, user_id: str | None = None) -> list[str]:
    if path is not None: return _legacy_read(path)
    if not user_id: raise ValueError("user_id is required")
    return _repo.list(user_id)


def add(ticker: str, path: Path | None = None, *, user_id: str | None = None) -> list[str]:
    if path is not None:
        ticker=ticker.strip().upper(); items=_legacy_read(path)
        if ticker and ticker not in items: items.append(ticker); path.write_text(json.dumps(items))
        return items
    if not user_id: raise ValueError("user_id is required")
    return _repo.add(user_id, ticker)


def remove(ticker: str, path: Path | None = None, *, user_id: str | None = None) -> list[str]:
    if path is not None:
        items=[t for t in _legacy_read(path) if t != ticker.strip().upper()]; path.write_text(json.dumps(items)); return items
    if not user_id: raise ValueError("user_id is required")
    return _repo.remove(user_id, ticker)


def is_tracked(ticker: str, path: Path | None = None, *, user_id: str | None = None) -> bool:
    return ticker.strip().upper() in load(path, user_id=user_id)


def assignment_is_active(ticker: str, *, user_id: str) -> bool:
    """Revalidate both ownership membership and active-user status."""
    return AuthService().session_user(user_id) is not None and is_tracked(
        ticker, user_id=user_id
    )


def lock_active_assignment(session, ticker: str, *, user_id: str) -> bool:
    return _repo.lock_active_assignment(session, user_id, ticker)


def get_notice_level(ticker: str, *, user_id: str) -> int:
    return _repo.get_notice_level(user_id, ticker)


def set_notice_level(ticker: str, level: int, *, user_id: str) -> None:
    _repo.set_notice_level(user_id, ticker, level)


def claim_notice(ticker: str, score: int | float, *, user_id: str) -> tuple[int, str] | None:
    """Atomically update the saved level and claim any newly crossed notice."""
    level = notice_level(score)
    previous = _repo.claim_notice_level(user_id, ticker, level)
    return None if previous is None else new_notice(previous, score)


def notice_level(score: int | float) -> int:
    return max((lv for lv in LEVELS if score >= lv), default=0)


def new_notice(prev_level: int, score: int | float) -> tuple[int, str] | None:
    level=notice_level(score)
    return (level, f"{_ORDINAL[level]} notice — swing score reached {level}%+") if level > prev_level else None
