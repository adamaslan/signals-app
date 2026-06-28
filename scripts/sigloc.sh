#!/usr/bin/env bash
# sigloc — local integration test runner for signals-app
# Usage: ./scripts/sigloc.sh [TICKER] [extra python args]
#   AAPL is default ticker.
#   Pass --no-llm to skip the live LLM call in test 4.
#   Pass --log-level DEBUG for verbose output.
#
# Examples:
#   ./scripts/sigloc.sh
#   ./scripts/sigloc.sh NVDA
#   ./scripts/sigloc.sh TSLA --no-llm
#   ./scripts/sigloc.sh AAPL --log-level DEBUG

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.env"
    set +a
fi

export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"

exec mamba run -n signals-app python "$SCRIPT_DIR/sigloc.py" "$@"
