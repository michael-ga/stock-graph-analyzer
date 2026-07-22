#!/bin/sh
set -eu
docker compose exec -T db pg_isready \
  --username "${POSTGRES_USER:-stockapp}" \
  --dbname "${POSTGRES_DB:-stockanalyzer}"
curl --fail --silent --show-error http://127.0.0.1:8501/_stcore/health
docker compose exec -T radar-worker \
  python -m stockanalyzer.radar_worker --healthcheck
host_version=$(git rev-parse HEAD)
app_version=$(docker compose exec -T app printenv APP_VERSION)
worker_version=$(docker compose exec -T radar-worker printenv APP_VERSION)
printf '\nHost git HEAD: %s\n' "$host_version"
printf 'App APP_VERSION: %s\n' "$app_version"
printf 'Radar worker APP_VERSION: %s\n' "$worker_version"
case "$host_version:$app_version:$worker_version" in
  *[!0-9a-f:]*)
    echo "Engineering version mismatch: expected three matching full Git SHAs" >&2
    exit 1
    ;;
esac
if [ "${#host_version}" -ne 40 ] || [ "${#app_version}" -ne 40 ] || \
   [ "${#worker_version}" -ne 40 ]; then
  echo "Engineering version mismatch: expected three matching full Git SHAs" >&2
  exit 1
fi
if [ "$host_version" != "$app_version" ] || [ "$host_version" != "$worker_version" ]; then
  echo "Engineering version mismatch: host, app, and radar worker differ" >&2
  exit 1
fi
printf '\nApplication, database, and radar worker are healthy.\n'
if command -v tailscale >/dev/null 2>&1; then
  tailscale serve status 2>/dev/null || sudo tailscale serve status
fi
