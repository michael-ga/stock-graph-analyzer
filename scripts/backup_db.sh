#!/bin/sh
set -eu
umask 077
backup_dir=${BACKUP_DIR:-./backups}
mkdir -p "$backup_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$backup_dir/stockanalyzer-$stamp.dump"
cleanup() {
  docker compose exec -T db rm -f /tmp/backup.dump >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker compose exec -T db pg_dump \
  --username "${POSTGRES_USER:-stockapp}" \
  --dbname "${POSTGRES_DB:-stockanalyzer}" \
  --format=custom --no-owner --file=/tmp/backup.dump
docker compose cp db:/tmp/backup.dump "$out"
chmod 600 "$out"
printf '%s\n' "$out"
