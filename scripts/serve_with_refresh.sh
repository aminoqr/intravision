#!/usr/bin/env bash
# File: scripts/serve_with_refresh.sh
# Start uvicorn immediately; live Intra refresh runs in parallel in the background.
# First fetch fires at once (non-blocking). Further fetches every 120s.
# Ctrl+C / SIGTERM tears down the fetch subshell so it cannot keep burning quota.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

export PYTHONPATH=src

FETCH_PID=""

cleanup() {
  local status=$?
  if [[ -n "${FETCH_PID}" ]]; then
    if kill -0 "${FETCH_PID}" 2>/dev/null; then
      pkill -P "${FETCH_PID}" 2>/dev/null || true
      kill "${FETCH_PID}" 2>/dev/null || true
      wait "${FETCH_PID}" 2>/dev/null || true
    fi
    FETCH_PID=""
  fi
  exit "${status}"
}

trap cleanup EXIT INT TERM HUP

echo "==> live refresh loop starting in background (now, then every 120s)"
(
  make fetch-live || echo "!! initial live fetch failed (server keeps running)"
  while true; do
    sleep 120
    echo "==> scheduled live fetch ($(date -Iseconds 2>/dev/null || date))"
    make fetch-live || echo "!! live fetch failed (server keeps running)"
  done
) &
FETCH_PID=$!

echo "==> serving dashboard on http://0.0.0.0:8000  (Ctrl+C stops server + fetch loop)"
.venv/bin/python -m uvicorn ft.app:app --host 0.0.0.0 --port 8000 --reload
