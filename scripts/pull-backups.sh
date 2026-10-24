#!/usr/bin/env bash
# Pull hub backups off the VPS over SSH, verify each SHA-256 and keep the newest N here.
#   bash scripts/pull-backups.sh <ssh-host> <dest-dir> [keep=7]
# The VPS keeps only two archives (hub-backup.py); this copy is the off-VPS retention.
set -euo pipefail
host="${1:?Usage: pull-backups.sh <ssh-host> <dest-dir> [keep]}"
dest="${2:?Usage: pull-backups.sh <ssh-host> <dest-dir> [keep]}"
keep="${3:-7}"
[[ "$keep" =~ ^[1-9][0-9]*$ ]] || { echo 'keep must be a positive integer' >&2; exit 2; }
mkdir -p "$dest"
chmod 700 "$dest" 2>/dev/null || true
rsync -a --include='arena-*.tar.gz' --include='arena-*.tar.gz.sha256' --exclude='*' "$host:/var/backups/uet-arena/" "$dest/"
cd "$dest"
bad=0
for archive in arena-*.tar.gz; do
  [[ -e "$archive" ]] || { echo 'No backup on the VPS yet.' >&2; exit 1; }
  expected="$(cut -d' ' -f1 "$archive.sha256")"
  actual="$(sha256sum "$archive" | cut -d' ' -f1)"
  if [[ "$expected" != "$actual" ]]; then echo "SHA mismatch: $archive" >&2; bad=1; fi
done
# Names carry a UTC timestamp, so lexical order is chronological.
ls -1 arena-*.tar.gz | sort -r | tail -n +"$((keep + 1))" | while read -r old; do rm -f -- "$old" "$old.sha256"; done
echo "$(date -Is) kept $(ls -1 arena-*.tar.gz | wc -l) backup(s) in $dest; newest $(ls -1 arena-*.tar.gz | sort | tail -1)"
exit "$bad"
