"""Shared fixtures: an isolated SQLAlchemy database and a book owner.

Every test that persists trades gets its own throwaway SQLite file through the
same repositories production uses against PostgreSQL — nothing ever touches a
real database. Postgres-only behavior lives in ``test_postgres_integration.py``.
"""
from __future__ import annotations

import pytest

from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base
from stockanalyzer.db.session import configure_engine


@pytest.fixture()
def book_db(tmp_path):
    engine = configure_engine(f"sqlite:///{tmp_path / 'book.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def owner(book_db) -> str:
    """User id owning the virtual book / paper journal under test."""
    return AuthService().create_user("book-owner", "correct horse battery staple").id
