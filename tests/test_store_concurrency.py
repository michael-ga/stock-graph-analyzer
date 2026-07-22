"""The store is hit from several threads at once (Streamlit runs each `run_every`
fragment — the swing radar, the live panels — on its own ScriptRunner thread).

Before thread-local connections, those threads shared one sqlite3.Connection and
concurrent use corrupted it: `sqlite3.InterfaceError: bad parameter or other API
misuse` and `IndexError: tuple index out of range` in `_row_to_trade`. This test
reproduces that access pattern and asserts it now runs cleanly with no lost writes.
"""
from __future__ import annotations

import threading

from stockanalyzer import virtualbook as vb
from stockanalyzer.data import store


def test_concurrent_read_write_no_corruption(tmp_path):
    p = tmp_path / "book.db"
    store._conn(p)                      # create schema once on the main thread

    errors: list[tuple[int, str]] = []
    n_threads, per_thread = 8, 5

    def worker(tid: int) -> None:
        try:
            for i in range(per_thread):
                tk = f"T{tid}_{i}"
                vb.open_position(ticker=tk, trader="me", entry=10.0, stop=9.0,
                                 target=12.0, snapshot={"setup": "x"},
                                 now=1_000_000.0 + i, path=p)
                vb.mark(tk, 12.5, now=1_000_100.0 + i, path=p)   # read + write (close at target)
                vb.load(p)                                        # read the whole book
        except Exception as exc:        # a raise here is exactly the bug we fixed
            errors.append((tid, repr(exc)))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent DB access raised: {errors}"
    book = vb.load(p)
    ids = [t["id"] for t in book]
    assert len(book) == n_threads * per_thread     # no write was dropped
    assert len(set(ids)) == len(ids)               # and no row was corrupted/duplicated


def test_each_thread_gets_its_own_connection(tmp_path):
    p = tmp_path / "book.db"
    main_conn = store._conn(p)
    other: dict[str, object] = {}

    def grab() -> None:
        other["conn"] = store._conn(p)

    t = threading.Thread(target=grab)
    t.start()
    t.join()

    assert other["conn"] is not main_conn          # distinct connection per thread
