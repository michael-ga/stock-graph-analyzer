#!/bin/sh
set -eu
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "usage: RESTORE_CONFIRM=stockanalyzer $0 BACKUP.dump" >&2
  exit 2
fi
if [ "${RESTORE_CONFIRM:-}" != "stockanalyzer" ]; then
  echo "Refusing destructive restore: set RESTORE_CONFIRM=stockanalyzer" >&2
  exit 2
fi
backup=$1
was_running=false
case "$(docker compose ps --status running --services app)" in
  *app*) was_running=true ;;
esac
restore_succeeded=false
cleanup() {
  docker compose exec -T db rm -f /tmp/restore.dump >/dev/null 2>&1 || true
  if [ "$restore_succeeded" = true ] && [ "$was_running" = true ]; then
    docker compose start app >/dev/null
  elif [ "$restore_succeeded" != true ]; then
    echo "Restore failed; application remains stopped to avoid serving partial data." >&2
  fi
}
trap cleanup EXIT INT TERM
docker compose stop app
docker compose cp "$backup" db:/tmp/restore.dump
docker compose exec -T db pg_restore \
  --username "${POSTGRES_USER:-stockapp}" \
  --dbname "${POSTGRES_DB:-stockanalyzer}" \
  --clean --if-exists --no-owner --exit-on-error /tmp/restore.dump
restore_succeeded=true
