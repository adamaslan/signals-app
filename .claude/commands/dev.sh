#!/usr/bin/env bash
# Run both the FastAPI backend and Next.js frontend in parallel.
# Press Ctrl+C once to kill both processes.
#
# Usage:
#   ./dev.sh              # start both
#   ./dev.sh --no-llm     # sets DISABLE_LLM=1 in the backend env

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a && source "$SCRIPT_DIR/.env" && set +a
    echo "[dev] loaded .env"
fi

export SIGNALS_ENV="${SIGNALS_ENV:-local}"
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

if [[ "$1" == "--no-llm" ]]; then
    export DISABLE_LLM=1
    echo "[dev] LLM synthesis disabled"
fi

# Cleanup: kill both child processes on exit
cleanup() {
    echo ""
    echo "[dev] shutting down..."
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    echo "[dev] done"
}
trap cleanup EXIT INT TERM

# ── Backend (FastAPI on :8000) ──────────────────────────────────────────────
echo "[dev] starting backend on http://localhost:8000"
mamba run -n signals-app uvicorn signals_app.api.main:app \
    --reload \
    --host 127.0.0.1 \
    --port 8000 \
    --log-level info \
    2>&1 | sed 's/^/[api] /' &
BACKEND_PID=$!

# ── Frontend (Next.js on :3000) ─────────────────────────────────────────────
cd "$SCRIPT_DIR/web"
echo "[dev] starting frontend on http://localhost:3000"
npm run dev 2>&1 | sed 's/^/[web] /' &
FRONTEND_PID=$!

echo ""
echo "[dev] both services running:"
echo "      API  → http://localhost:8000/docs"
echo "      Web  → http://localhost:3000"
echo "      Press Ctrl+C to stop both"
echo ""

# Wait for either process to exit (crash = stop both via trap)
wait "$BACKEND_PID" "$FRONTEND_PID"
