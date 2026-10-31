---
name: project-map
description: Fast orientation for this repository — where code lives, which files are too big to read whole, the verified test/lint/gate commands, and the git/PR workflow (base branch, private/mbris branches, [ALGO] PRs). Use at the start of any coding, review, debugging or PR task in stock-graph-analyzer, before exploring the tree.
---

# project-map — read this instead of exploring

Personal Streamlit technical-analysis app + paper-trading bots, deployed on an
Intel NUC (Docker Compose + PostgreSQL 16 + Tailscale Serve). CLAUDE.md holds
the rules; this file holds the map and the commands so you don't re-derive them.

## Where things are

| Path | What |
|---|---|
| `app.py` (~3000 lines) | Streamlit UI, bot wiring (`_BOTS`, `_run_bots`, `_run_gap_bots`, `_run_reversal_bots`, `_radar_plan`). **Never Read whole** — `Grep -n` then `Read` with offset/limit. |
| `replay_page.py`, `replay_scan_page.py`, `ui_common.py` | Replay simulator UI pages |
| `stockanalyzer/analysis/` | indicators (hand-rolled), levels, trend, ORB, reversal, `asof.py` (no-lookahead frames) |
| `stockanalyzer/explain/swing.py` (~1100 lines) | swing plan, score, GO gate — the algorithm core |
| `stockanalyzer/verdict/`, `sentiment/`, `valuation/` | multi-timeframe verdict, news/analyst sentiment, DCF/multiples |
| `stockanalyzer/virtualbook.py`, `papertrade.py`, `manage.py` | book + journal APIs (keyword-only `user_id`), managed-exit rules |
| `stockanalyzer/radar_worker.py` | the only process that writes radar results; PostgreSQL advisory-lock lease |
| `stockanalyzer/replay.py`, `replay_scan.py` | headless replay; practice books via `replay.book(path)` sandbox |
| `stockanalyzer/db/` | `models.py`, `session.py`, `sandbox.py`, `repositories/*` → see skill `persistence-change` |
| `stockanalyzer/auth.py`, `user_management.py`, `config.py` | Argon2 login, admin ops, `Settings.from_env` |
| `alembic/versions/` | schema migrations (container runs `alembic upgrade head` on start) |
| `algolab/` | `analyze.py` post-mortem, `LEARNINGS.md` (~440 lines), `BOT_RULES.md` |
| `scripts/` | NUC ops (`update.sh`, `healthcheck.sh`, `rollback.sh`, `backup_db.sh`, `restore_db.sh`, `deploy_nuc.sh`), `algo_gate.py`/`algo_probe.py`, user/admin creation, legacy import |
| `tests/` | ~430 tests; `conftest.py` gives `book_db` / `owner`. **Never open** `tests/valuation/fixtures/*` (ta_golden.json is 34k lines). |

`stockanalyzer/data/store.py` (SQLite) **no longer exists** — any doc still citing
`store.<fn>` maps to `TradeRepository.<fn>` (see the 2026-10-31 LEARNINGS entry).

## LEARNINGS.md without reading 440 lines

Before algorithm work read only: `Standing guardrails`, `Ruled out / do not repeat`,
`Open hypotheses` (top ~100 lines) and the **last** dated entry
(`Grep -n "^### 20" algolab/LEARNINGS.md`, read the last one).

## Commands (verified)

The cloud setup script puts `.venv` (Python 3.12, locked deps) on PATH and
installs `pgtest`. If `python --version` says 3.13, the setup didn't run: use
`uv venv -p python3.12 .venv && uv pip install --python .venv/bin/python -r requirements.lock "pytest>=8,<9" "ruff>=0.9,<1" "PyYAML>=6,<7"`.

```bash
python -m pytest -q                       # full suite, ~90 s, 7 Postgres tests skipped
export POSTGRES_TEST_URL=$(pgtest)        # local PG16 on :5433 (2 s cold, instant warm)
python -m pytest -q tests/test_postgres_integration.py tests/test_book_concurrency.py
ruff check --select E9,F .                # what CI enforces (full ruleset has legacy debt)
python scripts/algo_gate.py --base origin/feat/main-safe-merge   # a few minutes, see skill algo-gate
DATABASE_URL=$(pgtest) alembic upgrade head && DATABASE_URL=$(pgtest) alembic check
```

Run a subset first (`-k`, single file) and the full suite once before pushing.

## Git / PR workflow (this user)

- **Base branch: `feat/main-safe-merge`** (`main` lags behind it). Start work with
  `git fetch origin feat/main-safe-merge && git checkout -b private/mbris/<topic> origin/feat/main-safe-merge`.
- Branch prefix **`private/mbris/`**; push with `git push -u origin <branch>`.
  The repo owner is `michael-ga`; the user (`mbrisx`) opens PRs, the owner merges.
- Open each PR **directly against `feat/main-safe-merge`**. Do **not** stack PRs:
  the owner merges a stacked PR into its base branch, which then drags the stacked
  change into the parent PR (happened with #22/#23).
- CI (`.github/workflows/ci.yml`): `pytest (SQLite)` + `PostgreSQL integration`
  (alembic upgrade→downgrade→upgrade→check, then the Postgres tests). Runs on PRs
  and on pushes to `main`/`dev`.
- Anything touching decisions, their inputs or bot execution → run `algo-gate`;
  BLOCKED ⇒ `[ALGO]` title + `## ⚠️ ALGORITHM CHANGE` section (CLAUDE.md), in its
  own PR, never merged without the user's explicit approval.
- No PR template.
- **Everything committed is in English**: code, comments, docs, skills, commit
  messages, PR titles/descriptions and GitHub comments (other contributors do not
  read French). Chat replies follow the user's own language preference.
