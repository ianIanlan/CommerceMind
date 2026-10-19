#!/bin/sh
set -eu

api_base_url="${COMMERCE_BASE_URL:-http://localhost:8000}"
ui_base_url="${COMMERCE_UI_BASE_URL:-}"

if [ -n "${ui_base_url}" ]; then
  echo "[optional] frontend"
  curl -fsS "${ui_base_url}/" >/dev/null
fi

echo "[1/3] backend health"
curl -fsS "${api_base_url}/health"

echo "\n[2/3] knowledge base"
curl -fsS "${api_base_url}/knowledge/stats"

echo "\n[3/3] owned demo order"
curl -fsS "${api_base_url}/commerce/orders/ORD-10002?user_id=demo-user"

echo "\nDocker smoke test passed."
