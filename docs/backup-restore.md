# Backup and restore

Database dumps contain personal trading history and Argon2 password hashes. The
scripts create mode-`0600` custom-format dumps under `./backups` by default.

```bash
BACKUP_DIR=/opt/stock-analyzer/backups ./scripts/backup_db.sh
RESTORE_CONFIRM=stockanalyzer ./scripts/restore_db.sh /opt/stock-analyzer/backups/stockanalyzer-....dump
```

Restore is destructive to matching objects. The restore script requires an explicit
confirmation value, stops the app, restores with `--exit-on-error`, and cleans up the
temporary dump. It restarts the app only after a successful restore and only if the app
was running beforehand; failures leave the app stopped to avoid serving partial data.
Take a final dump and verify the selected file
before running it. Perform a quarterly drill into a fresh disposable Compose project
and compare counts for users, analyses, trades, paper trades, watchlists, and
swing-watch rows.

Run disposable-cache retention with:

```bash
docker compose exec app python -m scripts.prune_history --intraday-days 30
```

Analysis/trade history is never deleted unless `--history-days` is explicitly set.
Store at least one encrypted off-host copy. Never include `.env` in database backups.
