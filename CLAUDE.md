# CLAUDE.md — Stock Analyzer

Personal technical-analysis app (Python / Streamlit). Entry point `app.py`;
engine in `stockanalyzer/` (`analysis/`, `explain/swing.py`, `verdict/`); virtual
paper-trading book + all persistence in `stockanalyzer/data/store.py` (SQLite
`trades.db`). Indicators are hand-rolled in `stockanalyzer/analysis/indicators.py`.

## Before changing the trading algorithm — READ `algolab/LEARNINGS.md`

`algolab/` is the algorithm-development learning loop. Its job is to stop us
re-deriving the same wrong conclusions each session.

1. Run `python algolab/analyze.py` for the current post-mortem.
2. Read `algolab/LEARNINGS.md` — check **Ruled out / do not repeat** and **Open
   hypotheses** before forming a theory.
3. Make small, reversible changes; run `pytest`.
4. Add a dated entry to `algolab/LEARNINGS.md` describing what changed, why, and
   how you'll know it worked. Challenge prior entries with new data; don't
   silently overwrite them.

**Statistical discipline (the recurring trap):** the book copies one plan across
several bots, so raw row counts overstate the sample. Judge the *algorithm* on the
deduped **idea-level** view (`store.algorithm_correctness`, grouped by
`cohort_id`), and the *bots* on `store.trade_stats`. Do not re-weight the score or
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

Use `.local/dev.db`, never the real `trades.db`. Migrate first with
`python -m alembic upgrade head` under the same `DATABASE_URL`.

## Tests

`pytest` (uses `STOCKANALYZER_DB` env for DB isolation — never writes the real
`trades.db`). Run the full suite before declaring a change done.

## Conventions

- Persistence is functional, module-level functions with a default `db_path`
  (see `store.py`); match that style.
- Schema changes go through a versioned migration in `store._ensure_schema`
  (bump `SCHEMA_VERSION`, add an `if current < N:` block with guarded `ALTER`s).
  The `_DDL` runs on every connect *before* migrations, so it must not reference a
  column that only a later migration adds.
