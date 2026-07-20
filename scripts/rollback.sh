#!/bin/sh
set -eu
if [ "${ROLLBACK_CONFIRM:-}" != "stockanalyzer" ]; then
  echo "Refusing rollback: set ROLLBACK_CONFIRM=stockanalyzer" >&2
  exit 2
fi
ref=${1:-}
if [ -z "$ref" ] && [ -f .last-deploy ]; then
  ref=$(cat .last-deploy)
fi
if [ -z "$ref" ]; then
  echo "usage: ROLLBACK_CONFIRM=stockanalyzer $0 COMMIT" >&2
  exit 2
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "Refusing rollback: working tree is not clean" >&2
  exit 2
fi
./scripts/backup_db.sh
git switch --detach "$ref"
docker compose build app
docker compose up -d
./scripts/healthcheck.sh
printf 'Rolled application code back to %s; database was not downgraded.\n' "$ref"
