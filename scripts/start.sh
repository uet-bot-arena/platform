#!/usr/bin/env bash
set -euo pipefail
arena_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if (( $# > 1 )); then echo 'Usage: bash scripts/start.sh [port]' >&2; exit 2; fi
arena_local_port="${1:-41810}"
[[ "$arena_local_port" =~ ^[0-9]{4,5}$ ]] && ((10#$arena_local_port >= 1024 && 10#$arena_local_port <= 65535)) || { echo 'Port must be 1024..65535' >&2; exit 2; }
arena_local_port="$((10#$arena_local_port))"
cd "$arena_project_root/arena-backend"
[[ -x .venv/bin/python ]] || { echo 'Run bash scripts/setup.sh first.' >&2; exit 1; }
export ARENA_API_ORIGIN="${ARENA_API_ORIGIN:-http://127.0.0.1:$arena_local_port}"
printf 'UET Bot Arena: %s/#/login\n' "$ARENA_API_ORIGIN"
exec .venv/bin/python -m arena_api serve --port "$arena_local_port"
