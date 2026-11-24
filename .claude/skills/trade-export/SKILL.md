---
name: trade-export
description: Fetch all trade data from the test server (swing paper-trade journal, live bot positions, closed bot trade history) as JSON + CSV into exports/ on this PC, fast and read-only — no browser, no Streamlit, no radar. Use when the user asks to download, export, pull, fetch or analyze trades / trade history / journal / bot results / "the data from the server".
---

# trade-export — pull trade data from the server in seconds

## Run it

```bash
sh scripts/fetch_trades.sh
```

It SSHes to the server and, by default (**sql mode**), reads straight from
PostgreSQL: one read-only `SELECT` through `psql` in the `db` container returns the
user's `paper_trades` and `trades` rows as JSON, and
`python -m scripts.export_trades --from-raw` turns them into files on this PC. Nothing
needs to be deployed on the server for this. `TRADES_MODE=app` instead runs
`scripts/export_trades.py` inside the `app` container (needs a build that has it).
Output goes to `exports/<YYYYMMDD-HHMMSS>/`; the newest folder's path is in
`exports/latest.txt`.

The database itself is never exposed: it stays on the compose `database` network
(no published port). Do not suggest opening one or tunnelling 5432.

Files (newest first, every stored field kept, the app's columns first):

| File | What | Key columns |
|---|---|---|
| `swing_journal.json/.csv` | paper-trade journal (radar propositions) | date, ticker, level, score, kind, setup, entry, stop, target, rr, status, result_pct |
| `bot_positions.json/.csv` | open + pending virtual positions | ticker, trader, status, opened_ts, stake, entry, stop, target, trigger |
| `bot_history.json/.csv` | closed virtual trades | closed_ts, ticker, trader, close_reason, stake, entry, exit_price, pnl_pct, pnl_usd, score, setup (+ mfe_pct, mae_pct, snapshot…) |
| `all.json` | all three + counts + export time | |

Timestamps are ISO-8601 UTC. Unrealized P&L is **not** in the export (it needs a
live price); compute it from `bot_positions` if asked.

After fetching, read the files from the folder in `exports/latest.txt` to answer
the user's question — prefer the JSON for analysis.

## First-time setup

`scripts/fetch_trades.sh` reads `.local/trade-export.env` (git-ignored). If it is
missing, ask the user for these values and write the file — never guess a host:

```sh
TRADES_SSH=user@host                      # key-based SSH to the server
TRADES_DIR=/path/to/repo/on/server        # folder holding the compose file
TRADES_USER=testadmin                     # app username to export
TRADES_COMPOSE="docker compose"           # add -p/-f if the test stack uses them
TRADES_MODE=sql                           # default; "app" needs export_trades.py deployed
```

The SSH user must be allowed to run `docker compose` on the server. In app mode,
`No module named scripts.export_trades` means the server's build predates it — use
sql mode. If the `trades` / `paper_trades` columns change (`db/models.py`), keep
`export_trades.from_raw` in step; `tests/test_export_trades.py` checks both modes
produce identical output.

## Without SSH (on the server itself)

```bash
docker compose exec -T app python -m scripts.export_trades --username testadmin > trades.zip
docker compose exec -T app python -m scripts.export_trades --username testadmin --format json > trades.json
```

Locally against a dev DB: `python -m scripts.export_trades --username X --out exports/dev`.

## Rules

- Read-only: never add write paths to `export_trades.py`; tests pin that it writes nothing.
- Never put passwords in `.local/trade-export.env`; SSH must use a key.
- Exports contain the user's trades — keep them in `exports/` (git-ignored), never commit.
