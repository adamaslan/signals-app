#!/usr/bin/env bash
set -e

# Run the signals-app locally via uvicorn inside the mamba environment.
# Loads .env if present, then starts uvicorn with hot-reload on port 8000.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load .env if it exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
    echo "Loaded .env"
fi

export SIGNALS_ENV="${SIGNALS_ENV:-local}"
export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"

echo "Starting signals-app (env=$SIGNALS_ENV) on port 8000..."
echo "Docs: http://localhost:8000/docs"

mamba run -n signals-app uvicorn signals_app.api.main:app \
    --reload \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info
