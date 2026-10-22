#!/usr/bin/env bash
# Cài Mailpit (SMTP giả cho test) và cloudflared (Quick Tunnel cho webhook GitHub)
# vào ~/.local/bin. Phiên bản và SHA-256 được ghim; file tải về sai SHA thì dừng.
set -euo pipefail
MAILPIT_VERSION=v1.31.6
MAILPIT_SHA256=10fa12ccc610f653d4ed4df848c1817f9935dcf4e9a6a458a19ab0fa02093f91
CLOUDFLARED_VERSION=2026.10.3
CLOUDFLARED_SHA256=053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0

[[ "$(uname -m)" == x86_64 ]] || { echo 'Chỉ có SHA ghim cho linux-amd64.' >&2; exit 1; }
bin="${HOME}/.local/bin"
mkdir -p "$bin"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fetch() { # url sha out
  curl -fsSL -o "$3" "$1"
  echo "$2  $3" | sha256sum -c --quiet - || { echo "SHA-256 sai: $1" >&2; exit 1; }
}

fetch "https://github.com/axllent/mailpit/releases/download/${MAILPIT_VERSION}/mailpit-linux-amd64.tar.gz" \
  "$MAILPIT_SHA256" "$work/mailpit.tar.gz"
tar -xzf "$work/mailpit.tar.gz" -C "$work" mailpit
install -m 755 "$work/mailpit" "$bin/mailpit"

fetch "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-amd64" \
  "$CLOUDFLARED_SHA256" "$work/cloudflared"
install -m 755 "$work/cloudflared" "$bin/cloudflared"

"$bin/mailpit" version
"$bin/cloudflared" --version
