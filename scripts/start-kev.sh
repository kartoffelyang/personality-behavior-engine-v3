#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${KEV_PYTHON:-.venv-kev/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo '请先按 docs/KEV_SETUP.md 创建 .venv-kev 并安装 KEV。' >&2
  exit 1
fi
exec "$PYTHON" -m kev.serve --run jaredpalmer/kev-0.8b@bf75a6a8848ea6960ff2ed108d9ed44c2941174f --host 127.0.0.1 --port 8009
