#!/bin/sh
set -eu
lines=${LOG_LINES:-200}
docker compose logs --timestamps --tail "$lines" app db
