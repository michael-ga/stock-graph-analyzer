"""The book is hit from several threads at once (Streamlit runs each `run_every`
fragment — the swing radar, the live panels — on its own ScriptRunner thread,
and the radar worker writes alongside the UI).

The old SQLite store once shared one connection across threads and corrupted it.
Repositories now use a session per operation from a pooled engine; this test
reproduces the access pattern and asserts no error and no lost/duplicated write,
on SQLite always and on PostgreSQL when ``POSTGRES_TEST_URL`` is configured.
"""
from __future__ import annotations

import os
import threading

import pytest

from stockanalyzer import virtualbook as vb
from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base
from stockanalyzer.db.session import configure_engine

_POSTGRES_URL = os.getenv("POSTGRES_TEST_URL", "")


@pytest.fixture(params=["sqlite", "postgres"])
def concurrent_owner(request, tmp_path):
    if request.param == "postgres":
        if not _POSTGRES_URL.startswith(("postgresql://", "postgresql+psycopg://")):
            pytest.skip("POSTGRES_TEST_URL is not configured")
        url = _POSTGRES_URL
    else:
        url = f"sqlite:///{tmp_path / 'book.db'}"
    engine = configure_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield AuthService().create_user("concurrent-owner", "correct horse battery staple").id
    if request.param == "postgres":
        Base.metadata.drop_all(engine)
    engine.dispose()


def test_concurrent_read_write_no_corruption(concurrent_owner):
    owner = concurrent_owner
    errors: list[tuple[int, str]] = []
    n_threads, per_thread = 8, 5

    def worker(tid: int) -> None:
        try:
            for i in range(per_thread):
                tk = f"T{tid}_{i}"
                vb.open_position(ticker=tk, trader="me", entry=10.0, stop=9.0,
                                 target=12.0, snapshot={"setup": "x"},
                                 now=1_000_000.0 + i, user_id=owner)
                vb.mark(tk, 12.5, now=1_000_100.0 + i, user_id=owner)  # close at target
                vb.load(user_id=owner)                                  # read the book
        except Exception as exc:        # a raise here is exactly the bug class we guard
            errors.append((tid, repr(exc)))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent DB access raised: {errors}"
    book = vb.load(user_id=owner)
    ids = [t["id"] for t in book]
    assert len(book) == n_threads * per_thread     # no write was dropped
    assert len(set(ids)) == len(ids)               # and no row was corrupted/duplicated
    assert all(t["close_reason"] == "target_hit" for t in book)
