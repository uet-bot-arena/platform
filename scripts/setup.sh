#!/usr/bin/env bash
# Set up the hub, optionally also build/install a local Linux grader rootfs.
set -euo pipefail
arena_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
arena_setup_grader=false
if [[ "${1:-}" == '--with-grader' && "$#" == 1 ]]; then
  arena_setup_grader=true
elif (( $# )); then
  echo 'Usage: bash scripts/setup.sh [--with-grader]' >&2; exit 2
fi
command -v python3 >/dev/null || { echo 'Python 3.11+ required.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
cd "$arena_project_root/arena-backend"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m arena_api migrate
if "$arena_setup_grader"; then
  bash "$arena_project_root/scripts/build-rootfs.sh" "$arena_project_root/build/rootfs"
  arena_rootfs="$(find "$arena_project_root/build/rootfs" -maxdepth 1 -name 'rootfs-*.tar.zst' -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
  .venv/bin/python -m arena.sandbox.bwrap install "$arena_rootfs"
fi
printf '\nHub setup complete. Bootstrap an admin, start the hub, then install a grader (docs/thiet-ke.md).\n'
