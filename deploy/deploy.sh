#!/usr/bin/env bash
# Инкрементальный деплой kvadstat: код + статика + юниты + nginx, БЕЗ
# пересоздания venv и БЕЗ живого скана (в отличие от install.sh, который
# только для первичной установки). Закрывает DEPLOY-GAP «статика свежая,
# бэкенд устарел» одной командой. Запускать из корня репозитория:
#
#   deploy/deploy.sh root@1.2.3.4
#
# Кастомный ssh (sshpass/ProxyJump): DEPLOY_SSH='sshpass -e ssh ...' deploy/deploy.sh host
set -euo pipefail

HOST=${1:?usage: deploy/deploy.sh user@host}
APP=/opt/kvadstat
SSH_CMD=${DEPLOY_SSH:-ssh}

cd "$(dirname "$0")/.."
[ -f pyproject.toml ] || { echo "запускать из корня репо"; exit 1; }

echo ">>> rsync code+static -> $HOST:$APP"
rsync -az --delete -e "$SSH_CMD" \
  --chown=kvadstat:kvadstat \
  --exclude='data/' --exclude='.git/' --exclude='venv/' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='*.egg-info' \
  ./ "$HOST:$APP/"

$SSH_CMD "$HOST" bash -s <<'REMOTE'
set -euo pipefail
APP=/opt/kvadstat

# pip — только если pyproject/lock реально менялись (sha256-штамп)
STAMP="$APP/.deps-stamp"
HASH=$(cat "$APP/pyproject.toml" "$APP/deploy/requirements.lock" | sha256sum | cut -d' ' -f1)
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$HASH" ]; then
  echo ">>> deps changed — pip install from lock"
  sudo -u kvadstat "$APP/venv/bin/pip" install -q -r "$APP/deploy/requirements.lock"
  sudo -u kvadstat "$APP/venv/bin/pip" install -q -e "$APP" --no-deps
  echo "$HASH" > "$STAMP"
else
  echo ">>> deps unchanged"
fi

# юниты — ставим только изменившиеся
UNITS_CHANGED=0
for u in kvadstat.service kvadstat-scan.service kvadstat-scan.timer \
         kvadstat-backup.service kvadstat-backup.timer \
         kvadstat-notify@.service kvadstat-freshness.service \
         kvadstat-freshness.timer; do
  if [ -f "$APP/deploy/$u" ] && ! cmp -s "$APP/deploy/$u" "/etc/systemd/system/$u"; then
    install -m 644 "$APP/deploy/$u" "/etc/systemd/system/$u"
    echo ">>> unit updated: $u"
    UNITS_CHANGED=1
  fi
done
if [ "$UNITS_CHANGED" = 1 ]; then
  systemctl daemon-reload
fi

systemctl restart kvadstat.service
sleep 2
systemctl is-active --quiet kvadstat.service && echo ">>> kvadstat.service active"

# nginx vhost — только при изменении
NGINX_CONF=/etc/nginx/sites-available/kvadstat.gorev.space
if ! cmp -s "$APP/deploy/nginx-kvadstat.gorev.space.conf" "$NGINX_CONF"; then
  install -m 644 "$APP/deploy/nginx-kvadstat.gorev.space.conf" "$NGINX_CONF"
  nginx -t
  systemctl reload nginx
  echo ">>> nginx conf updated + reloaded"
fi

curl -s -o /dev/null -w ">>> datasette smoke -> %{http_code}\n" \
  "http://127.0.0.1:5053/kvadstat/today_all.json?_size=1&_shape=array"
REMOTE

echo ">>> deploy done: https://kvadstat.gorev.space"
