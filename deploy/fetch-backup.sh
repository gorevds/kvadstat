#!/usr/bin/env bash
# Pull-ярус offsite-бэкапа: забирает СВЕЖАЙШИЙ kvadstat-*.db.gz с сервера
# на локальную машину. Запускать с рабочей станции (не с сервера):
#
#   deploy/fetch-backup.sh root@1.2.3.4 ~/backups/kvadstat
#
# Идея: каталог /opt/kvadstat/data/backups живёт на том же диске, что и БД;
# потерю машины переживает только копия снаружи. Если настроен rclone-пуш
# (KVADSTAT_OFFSITE_REMOTE в backup.sh) — этот скрипт дублирующий ярус.
#
# Для регулярного pull добавьте в crontab рабочей станции:
#   17 9 * * * /path/to/kvadstat/deploy/fetch-backup.sh root@SERVER ~/backups/kvadstat
set -euo pipefail

HOST=${1:?usage: fetch-backup.sh user@host [dest_dir]}
DEST=${2:-"$HOME/backups/kvadstat"}
REMOTE_DIR=${KVADSTAT_BACKUP_DIR:-/opt/kvadstat/data/backups}
KEEP=${KVADSTAT_FETCH_KEEP:-30}

mkdir -p "$DEST"

LATEST=$(ssh "$HOST" "ls -1t $REMOTE_DIR/kvadstat-*.db.gz 2>/dev/null | head -1")
if [ -z "$LATEST" ]; then
  echo "ERROR: на $HOST нет бэкапов в $REMOTE_DIR" >&2
  exit 1
fi

BASE=$(basename "$LATEST")
if [ -f "$DEST/$BASE" ]; then
  echo "уже есть: $DEST/$BASE"
else
  scp -q "$HOST:$LATEST" "$DEST/$BASE.part"
  gzip -t "$DEST/$BASE.part"
  mv "$DEST/$BASE.part" "$DEST/$BASE"
  echo "скачан: $DEST/$BASE ($(du -h "$DEST/$BASE" | cut -f1))"
fi

# локальная ротация pull-копий
find "$DEST" -maxdepth 1 -name 'kvadstat-*.db.gz' -type f -mtime "+${KEEP}" -delete
