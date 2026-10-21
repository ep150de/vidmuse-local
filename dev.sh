#!/usr/bin/env bash
# Start SceneMux API + Vite with the latest source (kills stale listeners first).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

API_PORT="${SERVER_PORT:-8000}"
WEB_PORT="${VITE_PORT:-5173}"

echo "==> Stopping anything on :$API_PORT and :$WEB_PORT"
fuser -k "${API_PORT}/tcp" 2>/dev/null || true
fuser -k "${WEB_PORT}/tcp" 2>/dev/null || true
sleep 0.5

if [[ ! -x server/.venv/bin/python ]]; then
  echo "Missing server/.venv — create it with: cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "==> Starting API on :$API_PORT (reload on Python changes)"
PYTHONPATH="$ROOT" server/.venv/bin/python -m uvicorn server.main:app \
  --host 0.0.0.0 \
  --port "$API_PORT" \
  --reload \
  --reload-dir server &
API_PID=$!

cleanup() {
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting Vite on :$WEB_PORT (HMR for React)"
cd client
if [[ ! -d node_modules ]]; then
  npm install
fi
VITE_SERVER_URL="${VITE_SERVER_URL:-http://localhost:$API_PORT}" npm run dev -- --port "$WEB_PORT" --host
