#!/usr/bin/env bash
# OnFailure-хук: kvadstat-notify@<unit>.service вызывает `notify.sh <unit>`.
# Шлёт алерт в Telegram (если настроен /etc/kvadstat/notify.env) и всегда
# дублирует в journal через logger.
#
# /etc/kvadstat/notify.env (root:root, 0600), НЕ в репозитории:
#   TG_BOT_TOKEN=123456:ABC...
#   TG_CHAT_ID=123456789
set -euo pipefail

UNIT="${1:-unknown}"
ENV_FILE=/etc/kvadstat/notify.env

TAIL=$(journalctl -u "$UNIT" -n 10 --no-pager -o cat 2>/dev/null | tail -c 1500 || true)
MSG="❌ kvadstat: юнит ${UNIT} упал на $(hostname) ($(date '+%F %T %Z'))

${TAIL}"

logger -t kvadstat-notify "unit ${UNIT} failed" || true

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
    curl -fsS -m 15 "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT_ID}" \
      --data-urlencode "text=${MSG}" >/dev/null \
      || logger -t kvadstat-notify "telegram send failed for ${UNIT}"
  else
    logger -t kvadstat-notify "notify.env present but TG_* not set — alert only in journal"
  fi
else
  logger -t kvadstat-notify "no ${ENV_FILE} — alert only in journal"
fi
