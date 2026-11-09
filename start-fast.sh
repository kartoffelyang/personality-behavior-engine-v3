#!/bin/zsh
set -eu
cd "$(dirname "$0")"
exec uv run --python 3.12 --with Flask --with PyYAML --with requests --with 'httpx>=0.28,<1' python web/app.py --host 127.0.0.1 --port 10086
