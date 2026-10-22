#!/usr/bin/env bash
# Chạy Mailpit cho test: SMTP 127.0.0.1:1025, web/API 127.0.0.1:8025.
# Dùng: bash scripts/mailpit.sh start|stop|status
set -euo pipefail
pidfile="${XDG_RUNTIME_DIR:-/tmp}/uet-arena-mailpit.pid"
data="${HOME}/.local/share/uet-arena/mailpit.db"
case "${1:-start}" in
  start)
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then echo "Mailpit đang chạy"; exit 0; fi
    if curl -fsS -o /dev/null http://127.0.0.1:8025/api/v1/info 2>/dev/null; then echo "Mailpit đang chạy (tiến trình khác)"; exit 0; fi
    mkdir -p "$(dirname "$data")"
    setsid nohup "${HOME}/.local/bin/mailpit" --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025 \
      --database "$data" --max 5000 >/dev/null 2>&1 < /dev/null &
    echo $! > "$pidfile"
    for _ in $(seq 50); do curl -fsS -o /dev/null http://127.0.0.1:8025/api/v1/info 2>/dev/null && { echo "Mailpit: http://127.0.0.1:8025"; exit 0; }; sleep 0.1; done
    echo "Mailpit không khởi động được" >&2; exit 1;;
  stop)
    [[ -f "$pidfile" ]] && kill "$(cat "$pidfile")" 2>/dev/null || pkill -x mailpit || true
    rm -f "$pidfile";;
  status)
    curl -fsS http://127.0.0.1:8025/api/v1/info >/dev/null && echo "đang chạy" || { echo "đã dừng"; exit 1; };;
  *) echo "Dùng: $0 start|stop|status" >&2; exit 2;;
esac
