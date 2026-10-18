#!/bin/sh
set -eu

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required on the NUC." >&2
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required on the NUC." >&2
  exit 2
fi
if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale is required on the NUC." >&2
  exit 2
fi

if [ ! -f .env ]; then
  umask 077
  password=$(openssl rand -hex 32)
  {
    printf '%s\n' 'APP_ENV=production'
    printf '%s\n' 'POSTGRES_USER=stockapp'
    printf '%s\n' 'POSTGRES_DB=stockanalyzer'
    printf 'POSTGRES_PASSWORD=%s\n' "$password"
    printf 'DATABASE_URL=postgresql+psycopg://stockapp:%s@db:5432/stockanalyzer\n' "$password"
    printf '%s\n' 'FINNHUB_KEY='
    printf '%s\n' 'TWELVEDATA_KEY='
  } > .env
  chmod 600 .env
  unset password
  echo "Created private .env with a generated database password."
else
  chmod 600 .env
  echo "Using existing .env."
fi

mkdir -p backups legacy
docker compose build app
docker compose up -d
timeout=180
while [ "$timeout" -gt 0 ]; do
  if curl --fail --silent http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then
    break
  fi
  sleep 3
  timeout=$((timeout - 3))
done
if [ "$timeout" -le 0 ]; then
  docker compose logs --tail 100 app db
  echo "Application did not become healthy." >&2
  exit 1
fi

if [ "${SKIP_ADMIN:-0}" != "1" ]; then
  printf '\nCreate the administrator account now. The password is entered interactively.\n'
  docker compose exec app python -m scripts.create_admin --username "${ADMIN_USERNAME:-admin}"
fi

if tailscale serve --bg http://127.0.0.1:8501; then
  :
elif sudo tailscale serve --bg http://127.0.0.1:8501; then
  :
else
  echo "Could not configure Tailscale Serve." >&2
  exit 1
fi

./scripts/healthcheck.sh
printf '\nPrivate URL/status:\n'
tailscale serve status 2>/dev/null || sudo tailscale serve status
