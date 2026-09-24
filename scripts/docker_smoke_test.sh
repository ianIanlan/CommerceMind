#!/bin/sh
set -eu

base_url="${COMMERCE_BASE_URL:-http://localhost}"

echo "[1/4] frontend"
curl -fsS "${base_url}/" >/dev/null

echo "[2/4] backend health through frontend proxy"
curl -fsS "${base_url}/api/python/health"

echo "\n[3/4] knowledge base"
curl -fsS "${base_url}/api/python/knowledge/stats"

echo "\n[4/4] owned demo order"
curl -fsS "${base_url}/api/python/commerce/orders/ORD-10002?user_id=demo-user"

echo "\nDocker smoke test passed."
