#!/usr/bin/env bash
# One-command launcher: sets up Python deps on first run, migrates the DB,
# starts the backend, then opens the companion app. Stopping the app stops the backend.
# COMPANION_CMD overrides the app command (default: ./companion/desktop-companion).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${APP_PORT:-8000}"
PY="${PYTHON:-python3}"
COMPANION_CMD="${COMPANION_CMD:-./companion/desktop-companion}"

"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { echo "Python 3.10+ is required (found: $("$PY" --version 2>&1)). Install it from https://python.org"; exit 1; }

if [ ! -x .venv/bin/python ]; then
  echo "First run: installing Python dependencies (a few minutes)..."
  "$PY" -m venv .venv
  .venv/bin/pip install --quiet .
fi

.venv/bin/python -m alembic upgrade head

if curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "Backend already running on port $PORT."
else
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >> mail-agent.log 2>&1 &
  BACKEND_PID=$!
  trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT
  for _ in $(seq 1 30); do
    curl -fsS -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
    kill -0 "$BACKEND_PID" 2>/dev/null || { echo "Backend failed to start; see mail-agent.log"; exit 1; }
    sleep 1
  done
fi

if command -v "${COMPANION_CMD%% *}" >/dev/null 2>&1 || [ -x "${COMPANION_CMD%% *}" ]; then
  echo "Backend ready. Opening the companion..."
  $COMPANION_CMD
else
  echo "Backend ready. Open the Mail Agent Companion app now. Press Enter here to stop the backend."
  read -r _
fi
