#!/usr/bin/env bash
# Portable compiler install in this experiment only; no sudo or shell profile edits.
set -euo pipefail
cd -- "$(dirname -- "$0")"
mkdir -p .tools/compiler
archive='.tools/codon-linux-x86_64-v0.20.3.tar.gz'
if [[ ! -f "$archive" ]]; then
  curl --fail --location --connect-timeout 15 --max-time 180 \
    'https://github.com/exaloop/codon/releases/download/v0.20.3/codon-linux-x86_64.tar.gz' \
    --output "$archive"
fi
printf '%s  %s\n' '8716e5753a441764d13fd6a0b4f911060d292f66c9e61d0a4cdccb64c843da6f' "$archive" | sha256sum --check
tar -xzf "$archive" -C .tools/compiler --strip-components=1
.tools/compiler/bin/codon --version
