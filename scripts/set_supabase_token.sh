#!/usr/bin/env bash
# Run this yourself in a terminal: bash scripts/set_supabase_token.sh
# Prompts for your Supabase personal access token (hidden input, never echoed)
# and writes it to .env in the repo root. Get a token at:
#   https://supabase.com/dashboard/account/tokens
#
# This script never prints the token, and nothing it does sends the value
# anywhere but the local .env file (which is gitignored).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if grep -q "^\.env$" "$REPO_ROOT/.gitignore" 2>/dev/null; then
  :
else
  echo "WARNING: .env is not in .gitignore — aborting to avoid committing a secret." >&2
  exit 1
fi

read -r -s -p "Paste your Supabase access token (input hidden): " TOKEN
echo
if [ -z "$TOKEN" ]; then
  echo "No token entered — aborting." >&2
  exit 1
fi

if [ -f "$ENV_FILE" ] && grep -q "^SUPABASE_ACCESS_TOKEN=" "$ENV_FILE"; then
  # Replace existing line
  tmp="$(mktemp)"
  grep -v "^SUPABASE_ACCESS_TOKEN=" "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
fi

printf 'SUPABASE_ACCESS_TOKEN=%s\n' "$TOKEN" >> "$ENV_FILE"
unset TOKEN

echo "Saved to $ENV_FILE (value not displayed)."
echo "Next: run 'set -a; source .env; set +a' in this shell, then re-run the Supabase CLI steps."
