# PostgreSQL service

The database is reachable only on Compose's internal `database` network. It has no
host `ports` mapping. Durable state is stored in the `postgres_data` named volume.
Use `scripts/backup_db.sh` and `scripts/restore_db.sh`; do not copy live volume files.
