#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
action=${1:-start}
case "$action" in start|stop|status|logs|backup) ;; *) echo "Usage: ./start-linux.sh [start|stop|status|logs|backup]" >&2; exit 2;; esac

if ! command -v docker >/dev/null 2>&1; then
  echo "Install Docker Engine with Compose, then run this launcher again." >&2
  echo "Guide: docs/local-setup.md — https://docs.docker.com/engine/install/" >&2
  exit 2
fi
if ! docker compose version >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Docker must be running and accessible to your user, with Compose v2 installed." >&2
  echo "See docs/local-setup.md, then run this launcher again." >&2
  exit 2
fi
umask 077
mkdir -p .local
if [ ! -f .local/.env ]; then
  if [ "$action" != start ]; then echo "Run the launcher with start first." >&2; exit 2; fi
  password=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  { printf 'LOCAL_DB_PASSWORD=%s\n' "$password"
    printf 'LOCAL_PORT=%s\n' "${STOCK_LOCAL_PORT:-8501}"
    printf '%s\n' 'FINNHUB_KEY=' 'TWELVEDATA_KEY='
  } > .local/.env
  unset password
fi
chmod 600 .local/.env
# Distinguish a clean release from an edited checkout or a downloaded ZIP.
if command -v git >/dev/null 2>&1 && git rev-parse --verify HEAD >/dev/null 2>&1; then
  LOCAL_APP_VERSION=$(git rev-parse HEAD)
  if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then LOCAL_APP_VERSION="$LOCAL_APP_VERSION+local"; fi
else
  LOCAL_APP_VERSION=development
fi
export LOCAL_APP_VERSION
compose() { docker compose --project-name stockanalyzer-local --env-file .local/.env --file compose.local.yaml "$@"; }
case "$action" in
  start)
    compose up -d --build --wait --wait-timeout 180
    compose exec app python -m scripts.bootstrap_admin
    port=$(sed -n 's/^LOCAL_PORT=//p' .local/.env | tr -d '\r')
    url="http://localhost:${port:-8501}"
    echo "App ready: $url"
    echo "Create teammates through Users in the app. Stop with ./start-linux.sh stop."
    if command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true; fi
    ;;
  stop) compose stop ;;
  status) compose ps ;;
  logs) compose logs --tail 100 app radar-worker ;;
  backup)
    mkdir -p .local/backups
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    compose exec -T db pg_dump -U stockapp -d stockanalyzer -Fc -f /tmp/local-backup.dump
    compose cp db:/tmp/local-backup.dump ".local/backups/stockanalyzer-$stamp.dump"
    compose exec -T db rm -f /tmp/local-backup.dump
    echo "Backup saved: .local/backups/stockanalyzer-$stamp.dump"
    ;;
esac
