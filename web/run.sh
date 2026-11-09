#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-10086}"
echo "Starting review UI on http://${HOST}:${PORT}"
exec python3 web/app.py --host "$HOST" --port "$PORT"
