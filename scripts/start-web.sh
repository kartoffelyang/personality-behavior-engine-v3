#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${WEB_PYTHON:-.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo '请先按 README 创建 .venv 并安装 requirements.txt。' >&2
  exit 1
fi
exec "$PYTHON" web/app.py --host 127.0.0.1 --port 10086
