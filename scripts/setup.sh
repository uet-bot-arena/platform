#!/usr/bin/env bash
# Install a fresh local checkout. Does not create default credentials.
set -euo pipefail
arena_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
arena_build_worker=true
if [[ "${1:-}" == '--skip-worker-build' && "$#" == 1 ]]; then
  arena_build_worker=false
elif (( $# )); then
  echo 'Usage: bash scripts/setup.sh [--skip-worker-build]' >&2
  exit 2
fi
for arena_command in python3 docker; do
  command -v "$arena_command" >/dev/null || { echo "Missing $arena_command. See INSTALL.md." >&2; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
[[ "$(docker info --format '{{.OSType}}')" == linux ]] || { echo 'Docker must run Linux containers.' >&2; exit 1; }
[[ "$(docker info --format '{{.CgroupVersion}}')" == 2 ]] || { echo 'Docker cgroup v2 is required.' >&2; exit 1; }
cd "$arena_project_root/arena-backend"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
if "$arena_build_worker"; then
  docker build -t uet-arena-worker:v1 -t uet-arena-worker:clock-v2 \
    -t uet-arena-worker:experimental-v1 -t uet-arena-worker:experimental-clock-v2 .
else
  for arena_image in v1 clock-v2 experimental-v1 experimental-clock-v2; do
    docker image inspect "uet-arena-worker:$arena_image" >/dev/null
  done
fi
.venv/bin/python -m arena_api migrate
printf '\nSetup complete. Next: create your admin with the hidden CLI prompt, then bash scripts/start.sh.\n'
