#!/usr/bin/env bash
# Run from a verified checkout; docs/MAY-CHAM.md describes the SHA-256 check.
set -euo pipefail
hub='' profile=default slots=1 rootfs='' expected='' uninstall=false
while (( $# )); do
  case "$1" in
    --hub|--profile|--slots|--rootfs|--sha256)
      (( $# >= 2 )) || { echo "Missing value: $1" >&2; exit 2; }
      case "$1" in
        --hub) hub="$2";; --profile) profile="$2";; --slots) slots="$2";;
        --rootfs) rootfs="$2";; --sha256) expected="$2";;
      esac; shift 2;;
    --uninstall) uninstall=true; shift;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done
[[ "$profile" =~ ^[a-zA-Z0-9_-]{1,40}$ && "$slots" =~ ^[0-9]+$ ]] && (( slots >= 1 && slots <= 64 )) || exit 2
base="${XDG_DATA_HOME:-$HOME/.local/share}/uet-arena"
install="$base/grader-install-$profile"
unit="uet-arena-grader-$profile.service"
unit_file="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$unit"
if "$uninstall"; then
  if [[ -f "$unit_file" ]]; then systemctl --user disable --now "$unit"; fi
  rm -f -- "$unit_file"
  rm -rf -- "$install"
  systemctl --user daemon-reload
  echo 'Grader removed; shared rootfs was kept.'
  exit
fi
[[ "$hub" =~ ^https?://[^[:space:]\"@?#]+$ ]] || { echo 'Supply --hub http(s)://host[:port].' >&2; exit 2; }
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ -n "$rootfs" && ! "$rootfs" =~ ^https:// ]]; then rootfs="$(realpath -- "$rootfs")"; fi
packages=()
command -v bwrap >/dev/null || packages+=(bubblewrap)
command -v systemd-run >/dev/null || packages+=(systemd)
command -v zstd >/dev/null || packages+=(zstd)
command -v taskset >/dev/null || packages+=(util-linux)
python3 -c 'import venv, ensurepip' 2>/dev/null || packages+=(python3-venv)
if (( ${#packages[@]} )); then
  sudo apt-get update
  sudo apt-get install --no-install-recommends -y "${packages[@]}"
fi
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
systemctl --user is-active --quiet "$unit" && { echo "Stop $unit before reinstalling." >&2; exit 1; }
# Ubuntu 23.10+ lets unprivileged programs create user namespaces only through an AppArmor
# profile, and bubblewrap needs them. WSL2 kernels have no such restriction.
if [[ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || true)" == 1 ]]; then
  apparmor_file=/etc/apparmor.d/uet-arena-bwrap
  apparmor_rule="$(printf 'abi <abi/4.0>,\ninclude <tunables/global>\n\nprofile uet-arena-bwrap %s flags=(unconfined) {\n  userns,\n\n  include if exists <local/uet-arena-bwrap>\n}' "$(command -v bwrap)")"
  if [[ "$(cat "$apparmor_file" 2>/dev/null || true)" != "$apparmor_rule" ]]; then
    echo 'This Linux restricts user namespaces: adding AppArmor profile uet-arena-bwrap for bubblewrap (sudo).'
    printf '%s\n' "$apparmor_rule" | sudo tee "$apparmor_file" >/dev/null
    sudo apparmor_parser -r "$apparmor_file"
  fi
fi
# --check only talks to the hub, so prove the sandbox and the cgroup limits work before installing.
bwrap --unshare-all --ro-bind / / --dev /dev --proc /proc true \
  || { echo 'bubblewrap cannot create a sandbox here (user namespaces blocked); see docs/MAY-CHAM.md.' >&2; exit 1; }
systemd-run --user --scope --quiet -p MemoryMax=64M -p MemorySwapMax=0 -p CPUQuota=100% -p TasksMax=16 true \
  || { echo 'systemd user manager or cgroup v2 delegation is missing; see docs/MAY-CHAM.md.' >&2; exit 1; }
# Escape paths for systemd's unit syntax (not shell syntax).
unit_quote() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//%/%%}"; s="${s//\$/\$\$}"; printf '"%s"' "$s"; }
mkdir -p "$install/app" "$(dirname -- "$unit_file")"
cp -a "$repo/arena-backend/arena" "$repo/arena-backend/sdk" "$install/app/"
python3 -m venv --without-pip "$install/venv"
python="$install/venv/bin/python"
cd "$install/app"
if [[ -n "$rootfs" ]]; then
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo 'Supply the trusted rootfs --sha256 (64 hex digits).' >&2; exit 2; }
  archive="$install/rootfs-$expected.tar.zst"
  if [[ "$rootfs" =~ ^https:// ]]; then
    curl --fail --location --proto '=https' --proto-redir '=https' --output "$archive" "$rootfs"
  else
    cp -- "$rootfs" "$archive"
  fi
  printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --status
  "$python" -m arena.sandbox.bwrap install "$archive"
  rm -- "$archive"
fi
"$python" -m arena.grader --hub "$hub" --backend bwrap --slots "$slots" --profile "$profile" --token-file "$install/grader-token" --check
chmod 600 "$install/grader-token"
{
  printf '[Unit]\nDescription=UET Bot Arena grader %s\nAfter=network.target\n\n[Service]\n' "$profile"
  printf 'Type=exec\nWorkingDirectory=%s/app\n' "${install//%/%%}"
  printf 'LoadCredential=grader-token:%s/grader-token\n' "${install//%/%%}"
  printf 'ExecStart=%s -m arena.grader --backend bwrap --hub %s --slots %s --profile %s --state-dir %s\n' \
    "$(unit_quote "$python")" "$(unit_quote "$hub")" "$slots" "$profile" "$(unit_quote "$install/state")"
  printf 'Restart=on-failure\nRestartPreventExitStatus=3\nRestartSec=5\nTimeoutStopSec=180\nKillMode=mixed\n\n[Install]\nWantedBy=default.target\n'
} > "$unit_file"
systemctl --user daemon-reload
systemctl --user enable --now "$unit"
systemctl --user is-active --quiet "$unit"
echo "Grader installed. Logs: journalctl --user -u $unit -f"
