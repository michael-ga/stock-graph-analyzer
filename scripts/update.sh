#!/bin/sh
set -eu
if [ -n "$(git status --porcelain)" ]; then
  echo "Refusing update: working tree is not clean" >&2
  exit 2
fi
old=$(git rev-parse HEAD)
printf '%s\n' "$old" > .last-deploy
./scripts/backup_db.sh
git fetch origin
branch=$(git branch --show-current)
if [ -z "$branch" ]; then
  echo "Refusing update from detached HEAD" >&2
  exit 2
fi
git pull --ff-only origin "$branch"
APP_VERSION=$(git rev-parse HEAD)
export APP_VERSION
docker compose build --pull app
docker compose up -d
./scripts/healthcheck.sh
printf 'Updated from %s to %s\n' "$old" "$(git rev-parse HEAD)"
