#!/usr/bin/env bash
# Start the Clever Process API.
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck disable=SC1091
source venv/bin/activate

[ -f .env ] || cp .env.example .env

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8000}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
