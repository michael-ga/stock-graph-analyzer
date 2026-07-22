#!/bin/sh
set -eu
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
alembic upgrade head
exec streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port=8501 \
  --server.headless=true \
  --browser.gatherUsageStats=false
