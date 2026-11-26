---
name: persistence-change
description: Recipe for any database or persistence change in stock-graph-analyzer — adding a table/column, a repository method, a per-user query, a migration, an isolated sandbox book, or tests that touch the DB. Use whenever a task mentions models, Alembic, PostgreSQL, SQLite, repositories, the virtual book, paper trades, the replay book or legacy data import.
---

# persistence-change — the one persistence path

All state is SQLAlchemy 2.0 on PostgreSQL 16 in production (`DATABASE_URL`).
SQLite exists **only** for tests and for throwaway sandbox books. Never add a
SQLite-only code path or a `path=`/`db_path=` parameter to a domain API.

## Layers (match them)

```
domain module (functional API, keyword-only user_id)   virtualbook.py, papertrade.py, watchlist.py, swingwatch.py
  └─ repository class, one session_scope() per method   db/repositories/<name>.py
       └─ models                                         db/models.py
            └─ session plumbing                          db/session.py
```

- `session_scope()` — commit/rollback per operation; joins an ambient
  `transaction_scope()` if one is open (atomic multi-repo writes, e.g. the radar worker).
- `transaction_scope(join_existing=False)` — nested use raises unless `join_existing=True`.
- `bound_engine(engine)` — context-local redirect of every repository to another
  engine (ContextVar; other threads unaffected; hides the ambient session).
- `db/sandbox.py` `sandbox_book(path)` — a SQLite file with the full schema and an
  inactive owner (`SANDBOX_USER_ID`), bound via `bound_engine`; re-entrant per
  path; `release(path)` disposes it. The replay uses it through `replay.book(db)` /
  `replay.load_book(db)`. A replay must never create the application engine
  (pinned in `tests/test_replay_book.py`).
- Every user-owned row carries `user_id`; every repository method filters on it.
  Trades: unique partial index on active `(user_id, ticker, trader)`.
  Book analytics live on `TradeRepository`: `stats` / pure `trade_stats(rows)`,
  `algorithm_correctness` (idea level, by `cohort_id`, excludes managed),
  `managed_vs_fixed`, `losing_trade_patterns`, `closed_trades`.
- JSON columns (`snapshot`, indicator `payload`) are portable: aggregate them in
  Python, not with dialect-specific JSON SQL.

## Schema change checklist

1. Edit `stockanalyzer/db/models.py` (use `ID` for integer PKs: BigInteger on PG,
   Integer on SQLite; `server_default`/guarded backfill for NOT NULL columns on
   existing tables).
2. New revision in `alembic/versions/` named `YYYYMMDD_NN_<slug>.py`, `down_revision`
   = current head, with a working `downgrade()`.
3. Verify locally on PostgreSQL (same steps CI runs):
   ```bash
   export DATABASE_URL=$(pgtest)   # throwaway DB on :5433, never production
   alembic upgrade head && alembic downgrade base && alembic upgrade head && alembic check
   unset DATABASE_URL
   ```
   `alembic check` must say "No new upgrade operations detected".
4. Tests: the `book_db` + `owner` fixtures (`tests/conftest.py`) give a fresh
   SQLite DB + user per test. Put PostgreSQL-specific behavior (locking,
   `FOR UPDATE`, advisory locks, concurrency, partial indexes) in
   `tests/test_postgres_integration.py`, skipped unless `POSTGRES_TEST_URL` is set:
   `POSTGRES_TEST_URL=$(pgtest) python -m pytest -q tests/test_postgres_integration.py`.
5. If the change can alter what the book records for a bot (fills, exits, P&L,
   cohorts), it is bot execution: run `algo-gate` (see CLAUDE.md `[ALGO]` rule).

## Legacy data

`stockanalyzer/migrations/legacy.py` reads an old SQLite `trades.db` **read-only**
(signals/indicators/verdict/checks folded back into the snapshot);
`scripts/migrate_legacy_data.py --username U --sqlite F [--apply]` imports
idempotently (dry-run by default). Don't extend the legacy schema.

## Traps already hit

- Keep `DATABASE_URL` scoped to the one alembic/analyze command (the suite
  isolates itself either way, but a lingering export is how scripts end up
  writing to the wrong database).
- `configure_engine()` replaces the global engine: tests that call it own the
  global state for their duration (fixtures dispose it).
- Grouping by `snapshot["setup"]` must use `or "?"` (legacy rows have `None`;
  `sorted()` on mixed None/str raises).
