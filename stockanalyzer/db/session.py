"""SQLAlchemy engine and session-per-operation transaction boundary."""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None
_ambient_session: ContextVar[Session | None] = ContextVar("ambient_db_session", default=None)


def configure_engine(database_url: str | None = None) -> Engine:
    global _engine, _factory
    url = database_url or os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    if _engine is not None:
        _engine.dispose()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        configure_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _factory
    if _factory is None:
        configure_engine()
    assert _factory is not None
    return _factory


@contextmanager
def session_scope() -> Iterator[Session]:
    ambient = _ambient_session.get()
    if ambient is not None:
        yield ambient
        return
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def transaction_scope() -> Iterator[Session]:
    """Make repository operations in this context commit atomically."""
    if _ambient_session.get() is not None:
        raise RuntimeError("Nested transaction_scope is not supported")
    session = get_session_factory()()
    token = _ambient_session.set(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        _ambient_session.reset(token)
        session.close()
