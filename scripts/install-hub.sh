#!/usr/bin/env bash
# Ubuntu 24.04 hub only: no compiler, rootfs or bot execution.
set -euo pipefail
umask 077
arena_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
arena_origin=http://127.0.0.1:4181
arena_vcpus="$(nproc)"
arena_remove=false
while (( $# )); do
  case "$1" in
    --origin) arena_origin="${2:?Missing origin}"; shift 2 ;;
    --vcpus) arena_vcpus="${2:?Missing vCPU count}"; shift 2 ;;
    --uninstall) arena_remove=true; shift ;;
    *) echo 'Usage: sudo bash scripts/install-hub.sh [--origin https://host] [--vcpus N] [--uninstall]' >&2; exit 2 ;;
  esac
done
(( EUID == 0 )) || { echo 'Run with sudo/root.' >&2; exit 1; }
[[ "$arena_vcpus" =~ ^[1-9][0-9]?$ ]] || { echo 'vCPUs must be 1..99.' >&2; exit 2; }
if "$arena_remove"; then
  systemctl disable --now uet-arena-read@4183 uet-arena-read@4184 2>/dev/null || true
  rm -f /etc/systemd/system/uet-arena-read@.service
  systemctl disable --now uet-arena-backup.timer uet-arena-nginx.service uet-arena-hub.service uet-arena-publisher.service uet-arena-mailer.service 2>/dev/null || true
  for arena_unit in hub publisher mailer nginx backup; do
    rm -f "/etc/systemd/system/uet-arena-${arena_unit}.service"
  done
  rm -f /etc/systemd/system/uet-arena-backup.timer /etc/systemd/system/uet-arena.slice /etc/systemd/system/arena.slice
  rm -f /etc/systemd/journald.conf.d/uet-arena.conf
  systemctl daemon-reload
  systemctl restart systemd-journald
  echo 'Services removed. Data, backups, config, account and app kept for recovery.'
  exit
fi
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 && "$(uname -m)" == x86_64 ]] || { echo 'Ubuntu 24.04 amd64 required.' >&2; exit 1; }
python3 - "$arena_origin" <<'PY'
import sys
from urllib.parse import urlsplit
u=urlsplit(sys.argv[1])
assert u.scheme in ('http','https') and u.hostname and not u.username and not u.password
assert not u.query and not u.fragment and u.path==''
assert u.scheme=='https' or sys.argv[1]=='http://127.0.0.1:4181'
assert all(c.isalnum() or c in ':/.-' for c in sys.argv[1])
PY
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Prevent the distro's default nginx site from listening on port 80 during install.
arena_policy=false
if [[ ! -e /usr/sbin/policy-rc.d ]]; then
  printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
  chmod 755 /usr/sbin/policy-rc.d
  arena_policy=true
  trap 'rm -f /usr/sbin/policy-rc.d' EXIT
fi
apt-get install -y -qq python3 python3-venv ca-certificates curl openssl sqlite3 rsync nginx
if "$arena_policy"; then rm -f /usr/sbin/policy-rc.d; trap - EXIT; fi
systemctl disable --now nginx 2>/dev/null || true
if ! id arena >/dev/null 2>&1; then useradd --system --create-home --home-dir /var/lib/uet-arena --shell /usr/sbin/nologin arena; fi
if id -nG arena | tr ' ' '\n' | grep -Eq '^(sudo|admin)$'; then echo 'arena must not have sudo rights.' >&2; exit 1; fi
install -d -m 755 /opt/uet-arena/app
install -d -m 700 -o arena -g arena /var/lib/uet-arena /var/lib/uet-arena/data /etc/uet-arena
install -d -m 700 /var/backups/uet-arena
systemctl stop uet-arena-nginx uet-arena-publisher uet-arena-mailer uet-arena-hub 2>/dev/null || true
systemctl stop uet-arena-read@4183 uet-arena-read@4184 2>/dev/null || true
# Updating from the same installed tree needs no copy; rsync removes no data.
if [[ "$arena_source" != /opt/uet-arena/app ]]; then
  for arena_dir in arena-backend uet-bot-arena docs; do
    rsync -a --exclude=.venv --exclude=__pycache__ --exclude=test-results --exclude=.platform-state --exclude=node_modules --exclude=downloads \
      "$arena_source/$arena_dir" /opt/uet-arena/app/
  done
  install -d -m 755 /opt/uet-arena/app/scripts
  install -m 755 "$arena_source/scripts/hub-backup.py" /opt/uet-arena/app/scripts/
  install -m 755 "$arena_source/scripts/hub-status.py" /opt/uet-arena/app/scripts/
  install -m 755 "$arena_source/scripts/install-hub.sh" /opt/uet-arena/app/scripts/
  install -m 644 "$arena_source/scripts/hub-nginx.conf" "$arena_source/scripts/hub-nginx-security.conf" /opt/uet-arena/app/scripts/
fi
chown -R root:root /opt/uet-arena/app
chmod -R go-w,a+rX /opt/uet-arena/app
python3 -m venv /opt/uet-arena/venv
/opt/uet-arena/venv/bin/python -m pip install --disable-pip-version-check -r /opt/uet-arena/app/arena-backend/requirements.txt
chmod -R go-w,a+rX /opt/uet-arena/venv
arena_cloud_sha=053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0
if ! echo "$arena_cloud_sha  /usr/local/bin/cloudflared" | sha256sum -c --status 2>/dev/null; then
  arena_download="$(mktemp)"
  trap 'rm -f "$arena_download"' EXIT
  curl --fail --location --silent --show-error --retry 3 -o "$arena_download" https://github.com/cloudflare/cloudflared/releases/download/2026.10.3/cloudflared-linux-amd64
  echo "$arena_cloud_sha  $arena_download" | sha256sum -c --status || { echo 'cloudflared SHA mismatch.' >&2; exit 1; }
  install -m 755 "$arena_download" /usr/local/bin/cloudflared
  rm -f "$arena_download"; trap - EXIT
fi
arena_workers=$((arena_vcpus/2)); (( arena_workers >= 1 )) || arena_workers=1
if [[ ! -e /etc/uet-arena/config.toml ]]; then
  cat > /etc/uet-arena/config.toml <<EOF
[hub]
origin = "$arena_origin"
bind = "127.0.0.1"
port = 4181
data_dir = "/var/lib/uet-arena/data"
ui_root = "/opt/uet-arena/app/uet-bot-arena/dist"
cookie_secure = $([[ "$arena_origin" == https:* ]] && echo true || echo false)
hash_workers = $arena_workers
read_workers = $((arena_vcpus >= 4 ? 2 : 0))
trusted_proxies = ["127.0.0.1/32"]
EOF
fi
if [[ ! -e /etc/uet-arena/secrets.toml ]]; then
  python3 - <<'PY'
from pathlib import Path
import json,secrets
Path('/etc/uet-arena/secrets.toml').write_text('verification_hmac_key = '+json.dumps(secrets.token_urlsafe(32))+'\n')
PY
fi
chown arena:arena /etc/uet-arena/config.toml /etc/uet-arena/secrets.toml
chmod 600 /etc/uet-arena/config.toml /etc/uet-arena/secrets.toml
rm -f /etc/systemd/system/uet-arena.slice
cat > /etc/systemd/system/arena.slice <<EOF
[Unit]
Description=UET Arena combined hub, publisher, mailer and nginx budget
[Slice]
CPUAccounting=yes
MemoryAccounting=yes
CPUQuota=$((arena_vcpus*100))%
MemoryMax=2G
MemorySwapMax=0
TasksMax=1024
EOF
for arena_component in hub publisher mailer; do
  case "$arena_component" in
    hub) arena_command='arena_api serve --port 4182 --access-log'; arena_memory=1400M ;;
    publisher) arena_command='arena_api publish'; arena_memory=384M ;;
    mailer) arena_command='arena_api.mailer'; arena_memory=256M ;;
  esac
  cat > "/etc/systemd/system/uet-arena-${arena_component}.service" <<EOF
[Unit]
Description=UET Bot Arena $arena_component
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=10
[Service]
Type=simple
User=arena
Group=arena
WorkingDirectory=/opt/uet-arena/app/arena-backend
Environment=ARENA_CONFIG=/etc/uet-arena/config.toml
Environment=ARENA_SECRETS=/etc/uet-arena/secrets.toml
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/uet-arena/venv/bin/python -m $arena_command
Restart=on-failure
RestartSec=5
TimeoutStopSec=420
Slice=arena.slice
MemoryMax=$arena_memory
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/uet-arena
UMask=0077
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=30s
LogRateLimitBurst=10000
[Install]
WantedBy=multi-user.target
EOF
done
install -m 644 /opt/uet-arena/app/scripts/hub-nginx.conf /etc/uet-arena/nginx.conf
install -m 644 /opt/uet-arena/app/scripts/hub-nginx-security.conf /etc/uet-arena/nginx-security.conf
arena_read_workers="$(cd /opt/uet-arena/app/arena-backend && ARENA_CONFIG=/etc/uet-arena/config.toml ARENA_SECRETS=/etc/uet-arena/secrets.toml /opt/uet-arena/venv/bin/python -m arena_api.read_worker --nginx-dir /etc/uet-arena)"
cat > /etc/systemd/system/uet-arena-read@.service <<'EOF'
[Unit]
Description=UET Bot Arena read-only API on %i
After=uet-arena-hub.service
[Service]
User=arena
Group=arena
WorkingDirectory=/opt/uet-arena/app/arena-backend
Environment=ARENA_CONFIG=/etc/uet-arena/config.toml
Environment=ARENA_SECRETS=/etc/uet-arena/secrets.toml
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/uet-arena/venv/bin/python -m arena_api serve --port %i --read-only --access-log
Restart=on-failure
RestartSec=1
Slice=arena.slice
MemoryMax=384M
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/var/lib/uet-arena
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
for arena_index in 0 1; do
  arena_read_unit="uet-arena-read@$((4183+arena_index))"
  if (( arena_index < arena_read_workers )); then
    systemctl enable "$arena_read_unit"
  else
    systemctl disable "$arena_read_unit" 2>/dev/null || true
  fi
done
cat > /etc/systemd/system/uet-arena-nginx.service <<'EOF'
[Unit]
Description=UET Bot Arena nginx (loopback static files and API proxy)
After=network-online.target uet-arena-hub.service
[Service]
Type=simple
User=arena
Group=arena
RuntimeDirectory=uet-arena-nginx
RuntimeDirectoryMode=0700
ExecStartPre=/usr/sbin/nginx -t -c /etc/uet-arena/nginx.conf
ExecStart=/usr/sbin/nginx -c /etc/uet-arena/nginx.conf -g 'daemon off;'
LimitNOFILE=16384
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=2
Slice=arena.slice
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/var/lib/uet-arena
ReadWritePaths=/run/uet-arena-nginx
UMask=0077
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100000
[Install]
WantedBy=multi-user.target
EOF
install -d -m 755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/uet-arena.conf <<'EOF'
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=64M
MaxRetentionSec=7day
EOF
systemctl restart systemd-journald
cat > /etc/systemd/system/uet-arena-backup.service <<'EOF'
[Unit]
Description=Consistent UET Arena backup (brief service pause)
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/uet-arena/app/scripts/hub-backup.py backup
UMask=0077
EOF
cat > /etc/systemd/system/uet-arena-backup.timer <<'EOF'
[Unit]
Description=Daily UET Arena backup
[Timer]
OnCalendar=*-*-* 04:00:00 Asia/Bangkok
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
EOF
# Run from the installed package, not the caller's checkout.
(cd /opt/uet-arena/app/arena-backend && runuser -u arena -- env ARENA_CONFIG=/etc/uet-arena/config.toml ARENA_SECRETS=/etc/uet-arena/secrets.toml /opt/uet-arena/venv/bin/python -m arena_api migrate)
systemctl daemon-reload
systemctl enable --now uet-arena-hub uet-arena-publisher uet-arena-mailer uet-arena-nginx uet-arena-backup.timer
for ((arena_index=0; arena_index<arena_read_workers; arena_index++)); do
  systemctl start "uet-arena-read@$((4183+arena_index))"
  systemctl is-active --quiet "uet-arena-read@$((4183+arena_index))"
done
for arena_component in hub publisher mailer nginx; do
  systemctl is-active --quiet "uet-arena-$arena_component" || { echo "$arena_component failed to start; inspect its journal." >&2; exit 1; }
done
echo 'Installed. Existing config/secrets were preserved. Bootstrap an admin (docs/DEPLOY.md); SMTP and named tunnel need configuration.'
