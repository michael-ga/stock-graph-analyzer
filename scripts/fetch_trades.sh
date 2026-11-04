#!/bin/sh
# Pull one user's trade data from the server into exports/<timestamp>/ on this PC.
# Read-only on the server either way.
#
#   sql mode (default) — straight from PostgreSQL: one SELECT through psql in the
#                        `db` container. Needs nothing deployed on the server.
#   app mode           — runs scripts/export_trades.py in the `app` container
#                        (the server must run a build that has it).
#
# Settings come from .local/trade-export.env (git-ignored), e.g.
#   TRADES_SSH=shambik@nuc            # ssh destination (key-based login)
#   TRADES_DIR=/home/shambik/stock-graph-analyzer-test
#   TRADES_USER=testadmin             # app username whose trades to export
#   TRADES_COMPOSE="docker compose"   # e.g. "docker compose -p stock-test"
#   TRADES_MODE=sql                   # or: app
set -eu

cd "$(dirname "$0")/.."
CONF="${TRADES_CONF:-.local/trade-export.env}"
case "$CONF" in /*) ;; *) CONF="./$CONF" ;; esac
# shellcheck disable=SC1090
if [ -f "$CONF" ]; then . "$CONF"; fi
: "${TRADES_SSH:?set TRADES_SSH in $CONF}"
: "${TRADES_DIR:?set TRADES_DIR in $CONF}"
: "${TRADES_USER:?set TRADES_USER in $CONF}"
COMPOSE="${TRADES_COMPOSE:-docker compose}"
MODE="${TRADES_MODE:-sql}"

case "$TRADES_USER" in
  *[!A-Za-z0-9._@-]*|'') echo "TRADES_USER has unexpected characters" >&2; exit 2 ;;
esac

PY=.venv/Scripts/python.exe
[ -x "$PY" ] || PY=.venv/bin/python
[ -x "$PY" ] || PY=python

OUT="exports/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

if [ "$MODE" = sql ]; then
  # The query comes over stdin so psql's :'uname' quotes the username safely.
  # $POSTGRES_USER / $POSTGRES_DB expand inside the db container.
  ssh -o BatchMode=yes "$TRADES_SSH" \
    "cd '$TRADES_DIR' && $COMPOSE exec -T db sh -c 'psql -X -q -At -v ON_ERROR_STOP=1 -v uname=$TRADES_USER -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" \
    > "$OUT/raw.json" <<'SQL'
SET default_transaction_read_only = on;
SELECT json_build_object(
  'journal', COALESCE((SELECT json_agg(p ORDER BY p.ts DESC)
                       FROM paper_trades p WHERE p.user_id = u.id), '[]'::json),
  'book',    COALESCE((SELECT json_agg(t ORDER BY t.opened_ts DESC)
                       FROM trades t WHERE t.user_id = u.id), '[]'::json))
FROM users u
WHERE u.username = lower(:'uname');
SQL
  "$PY" -m scripts.export_trades --username "$TRADES_USER" \
    --from-raw "$OUT/raw.json" --out "$OUT"
  rm -f "$OUT/raw.json"
elif [ "$MODE" = app ]; then
  ssh -o BatchMode=yes "$TRADES_SSH" \
    "cd '$TRADES_DIR' && $COMPOSE exec -T app python -m scripts.export_trades --username '$TRADES_USER'" \
    > "$OUT/trades.zip"
  "$PY" -m zipfile -e "$OUT/trades.zip" "$OUT"
else
  echo "TRADES_MODE must be sql or app" >&2; exit 2
fi

echo "$OUT" > exports/latest.txt
echo "Saved to $OUT"
ls -l "$OUT"
