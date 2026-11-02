"""Isolated, throwaway SQLite books for simulations (the replay simulator).

A sandbox book is its own SQLite file with the full application schema and a
single inactive owner. ``sandbox_book(path)`` binds the repositories to it for the
duration of a ``with`` block (``session.bound_engine``), so the same
``virtualbook`` / ``TradeRepository`` code that runs the live book runs the
practice book — while practice trades can never reach the real database.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stockanalyzer.db.models import Base, User
from stockanalyzer.db.session import bound_engine

SANDBOX_USER_ID = "00000000-0000-4000-8000-000000000000"
_SANDBOX_USERNAME = "sandbox"

_engines: dict[str, Engine] = {}
_lock = threading.Lock()
_active: ContextVar[str | None] = ContextVar("active_sandbox_book", default=None)


def _key(path: Path) -> str:
    return str(Path(path).resolve())


def _engine(path: Path) -> Engine:
    key = _key(path)
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(f"sqlite:///{key}",
                                   connect_args={"check_same_thread": False})
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                if session.scalar(select(User.id).where(User.id == SANDBOX_USER_ID)) is None:
                    # Inactive and unhashable: the sandbox owner can never log in.
                    session.add(User(id=SANDBOX_USER_ID, username=_SANDBOX_USERNAME,
                                     password_hash="!sandbox", is_active=False))
                    session.commit()
            _engines[key] = engine
        return engine


@contextmanager
def sandbox_book(path: Path) -> Iterator[str]:
    """Bind repositories to the sandbox book at ``path``; yields its owner id.

    Re-entrant for the same path, so helpers can nest freely."""
    key = _key(path)
    if _active.get() == key:
        yield SANDBOX_USER_ID
        return
    token = _active.set(key)
    try:
        with bound_engine(_engine(path)):
            yield SANDBOX_USER_ID
    finally:
        _active.reset(token)


def release(path: Path) -> None:
    """Dispose the cached engine for ``path`` (short-lived books, e.g. a scan day),
    so no connection keeps the file open."""
    with _lock:
        engine = _engines.pop(_key(path), None)
    if engine is not None:
        engine.dispose()
