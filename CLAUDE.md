# CLAUDE.md — Stock Analyzer

Personal technical-analysis app (Python / Streamlit). Entry point `app.py`;
engine in `stockanalyzer/` (`analysis/`, `explain/swing.py`, `verdict/`); virtual
paper-trading book in `stockanalyzer/virtualbook.py` / `papertrade.py`. All
persistence is SQLAlchemy (`stockanalyzer/db/`: `models.py`, `session.py`,
`repositories/`) on PostgreSQL in production (`DATABASE_URL`); every book/journal
call is scoped by a keyword-only `user_id`. Indicators are hand-rolled in
`stockanalyzer/analysis/indicators.py`.

## Before changing the trading algorithm — READ `algolab/LEARNINGS.md`

`algolab/` is the algorithm-development learning loop. Its job is to stop us
re-deriving the same wrong conclusions each session.

1. Run `python algolab/analyze.py --user <name>` (needs `DATABASE_URL`) for the
   current post-mortem.
2. Read `algolab/LEARNINGS.md` — check **Ruled out / do not repeat** and **Open
   hypotheses** before forming a theory.
3. Make small, reversible changes; run `pytest`.
4. Add a dated entry to `algolab/LEARNINGS.md` describing what changed, why, and
   how you'll know it worked. Challenge prior entries with new data; don't
   silently overwrite them.

**Statistical discipline (the recurring trap):** the book copies one plan across
several bots, so raw row counts overstate the sample. Judge the *algorithm* on the
deduped **idea-level** view (`virtualbook.algorithm_correctness`, grouped by
`cohort_id`), and the *bots* on `virtualbook.stats`. Do not re-weight the score or
kill a setup on a single session's data — see the guardrails in `LEARNINGS.md`.

## Pull requests that change the algorithm — mark them `[ALGO]` (mandatory)

A change affects the algorithm if it can change any bot decision, **directly or
indirectly**: the swing plan or score, any bot's gates or thresholds, Gap-and-Go,
reversal or swing-bot rules, when or how bots execute, or any *input* those read
(prices, quotes, `prev_close`, sessions, the data feed, `pipeline.py`). When in
doubt, run the `algo-gate` skill; a `BLOCKED` verdict means it affects the algorithm.

Every such PR, whoever opens it:

1. **Title starts with `[ALGO]`**, e.g. `[ALGO] Measure the gap from the 09:30 open`.
2. **Description starts with a `## ⚠️ ALGORITHM CHANGE` section** that states, in detail:
   - which bots and decisions change, and which do **not**;
   - a before → after table of each changed rule, threshold or input;
   - each commit and the files and functions it changes;
   - the evidence: `algo-gate` verdict and, for paths the frozen probes cannot reach,
     a real-history replay A/B (trades per bot, before → after, the days that changed);
   - what is not covered and why;
   - the dated `algolab/LEARNINGS.md` entry it adds.
3. **Algorithm changes ship in their own PR**, never mixed into a non-algorithm PR.
   A PR without `[ALGO]` must contain no algorithm change, direct or indirect.

Never merge or enable auto-merge on an `[ALGO]` PR without the user's explicit
approval of that PR in the conversation.

## Local run — always start BOTH processes

The Streamlit app only displays radar results; the **radar worker** is the only
process that writes them. Starting the app alone leaves Swing radar stuck on
"preparing analysis". When running the app locally (no Docker), always start both
with the same environment:

```
APP_ENV=development DATABASE_URL=sqlite:///.local/dev.db .venv/Scripts/python.exe -m streamlit run app.py --server.port=8501
APP_ENV=development DATABASE_URL=sqlite:///.local/dev.db RADAR_WORKER_HEARTBEAT=.local/radar-worker-heartbeat .venv/Scripts/python.exe -m stockanalyzer.radar_worker
```

Use `.local/dev.db`, never a production database. Migrate first with
`python -m alembic upgrade head` under the same `DATABASE_URL`.

**Skip the password locally:** add `DEV_AUTO_LOGIN=<username>` to the app's
environment and the app signs that existing account in without the form. It works
only with `APP_ENV=development` **and** a SQLite `DATABASE_URL`; production and
PostgreSQL always ask for the password (`auth.dev_login_username`). "Log out"
shows the normal sign-in form for the rest of that browser session. Never set
`DEV_AUTO_LOGIN` on the NUC or in any shared environment.

## Tests

`pytest` — persistence tests run the real repositories against a throwaway SQLite
file per test (`tests/conftest.py`: `book_db` / `owner` fixtures); nothing touches
a real database. PostgreSQL-specific tests run when `POSTGRES_TEST_URL` is set
(CI does this, see `.github/workflows/ci.yml`). Run the full suite before
declaring a change done.

## Conventions

- Domain modules (`virtualbook`, `papertrade`, `watchlist`, …) stay functional
  module-level APIs; they delegate to a repository class in `db/repositories/`
  that opens a `session_scope()` per operation. Match that split.
- Schema changes: edit `db/models.py` **and** add an Alembic revision in
  `alembic/versions/` (the container runs `alembic upgrade head` on start). CI
  runs upgrade → downgrade → upgrade → `alembic check` on PostgreSQL, so models
  and migrations must not drift.
- Never add a SQLite-only code path; SQLite is for tests only.
- Everything committed is in English: code, comments, docs, skills, commit
  messages, PR titles/descriptions and GitHub comments.
