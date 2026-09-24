#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
export LOCAL_DEMO_MODE="${LOCAL_DEMO_MODE:-true}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
# /metrics is still exposed by FastAPI; avoid opening a second metrics socket.
export PROMETHEUS_PORT="0"
exec .runtime-venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port "${API_PORT:-8000}"
