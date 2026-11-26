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


@pytest.fixture(autouse=True)
def _no_etf_name_lookup(monkeypatch):
    """ETF auto-linking reads fund names from the network; tests never do."""
    from stockanalyzer import etf_links
    monkeypatch.setattr(etf_links, "_NAME_LOOKUP", lambda ticker: None)
    etf_links._attempted.clear()


@pytest.fixture(autouse=True)
def _no_setup_bot_network(monkeypatch):
    """The live setup bots read QQQ and fund names from the network; tests never do."""
    from stockanalyzer import setup_bots
    monkeypatch.setattr(setup_bots, "market_frame", lambda *a, **k: None)
    monkeypatch.setattr(setup_bots, "market_sign", lambda ticker: 1)


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
