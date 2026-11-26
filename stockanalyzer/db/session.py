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
# A context-local override of the session factory (see ``bound_engine``).
_bound_factory: ContextVar[sessionmaker[Session] | None] = ContextVar(
    "bound_db_session_factory", default=None)


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
    bound = _bound_factory.get()
    if bound is not None:
        return bound
    if _factory is None:
        configure_engine()
    assert _factory is not None
    return _factory


@contextmanager
def bound_engine(engine: Engine) -> Iterator[None]:
    """Route every repository call in this context to ``engine`` instead of the
    configured application database.

    Used for isolated sandboxes (the replay simulator's practice books): inside
    the block nothing can reach ``DATABASE_URL``, and the application engine is
    never created or reconfigured. Context-local, so other threads (the live
    app, the radar worker) keep using the real database. Any ambient
    ``transaction_scope`` session is hidden for the duration."""
    factory_token = _bound_factory.set(sessionmaker(bind=engine, expire_on_commit=False))
    ambient_token = _ambient_session.set(None)
    try:
        yield
    finally:
        _ambient_session.reset(ambient_token)
        _bound_factory.reset(factory_token)


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
def transaction_scope(*, join_existing: bool = False) -> Iterator[Session]:
    """Make repository operations in this context commit atomically."""
    ambient = _ambient_session.get()
    if ambient is not None and join_existing:
        yield ambient
        return
    if ambient is not None:
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
