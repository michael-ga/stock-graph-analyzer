#!/bin/sh
set -eu
docker compose exec -T db pg_isready \
  --username "${POSTGRES_USER:-stockapp}" \
  --dbname "${POSTGRES_DB:-stockanalyzer}"
curl --fail --silent --show-error http://127.0.0.1:8501/_stcore/health
printf '\nApplication and database are healthy.\n'
if command -v tailscale >/dev/null 2>&1; then
  tailscale serve status
fi
