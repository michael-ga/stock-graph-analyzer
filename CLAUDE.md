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
