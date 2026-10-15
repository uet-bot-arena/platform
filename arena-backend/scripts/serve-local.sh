#!/usr/bin/env bash
# Long-lived WSL controller; used by start-local.ps1 without a chat terminal.
set -euo pipefail
arena_port="${1:-41810}"
[[ "$arena_port" =~ ^[0-9]+$ ]] && ((arena_port >= 1024 && arena_port <= 65535)) || { echo 'Invalid port' >&2; exit 2; }
cd "$(dirname "$0")/.."
export ARENA_API_ORIGIN="http://127.0.0.1:$arena_port"
exec .venv/bin/python -m arena_api serve --port "$arena_port"
