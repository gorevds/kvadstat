#!/usr/bin/env bash
# Ежедневный бэкап kvadstat.db через sqlite3 ".backup" (online, безопасно
# с активным writer'ом) + проверка целостности + gzip + ротация (14 дней)
# + опциональный offsite-пуш.
#
# Запускается из kvadstat-backup.service (см. deploy/kvadstat-backup.service).
# RPO = 24ч (один бэкап в сутки), RTO ~30с (gunzip + sqlite restore).
#
# Локальный каталог backups/ живёт на том же диске, что и БД — он защищает
# только от логических ошибок (битый скан, кривая миграция). От потери
# машины защищает ТОЛЬКО offsite-ярус:
#   1) KVADSTAT_OFFSITE_REMOTE (rclone remote:path) — пуш с сервера, или
#   2) deploy/fetch-backup.sh — pull последнего бэкапа на другую машину.
set -euo pipefail

DB=${KVADSTAT_DB:-/opt/kvadstat/data/kvadstat.db}
DEST_DIR=${KVADSTAT_BACKUP_DIR:-/opt/kvadstat/data/backups}
KEEP_DAYS=${KVADSTAT_BACKUP_KEEP_DAYS:-14}
# rclone remote (например "b2:kvadstat-backups"); пусто = offsite-пуш выключен
OFFSITE_REMOTE=${KVADSTAT_OFFSITE_REMOTE:-}

mkdir -p "$DEST_DIR"

STAMP=$(date +%Y%m%d-%H%M)
TARGET="$DEST_DIR/kvadstat-${STAMP}.db"

# Упавший прогон не должен оставлять вечный недокачанный .db (ротация ниже
# чистит только *.db.gz) — убираем свой артефакт при любой ошибке.
trap 'rm -f "$TARGET"' ERR

# sqlite3 ".backup" безопаснее cp — снимает консистентный снэпшот через
# Backup API, не блокируя writer надолго (по 100 страниц за итерацию).
# `.timeout 30000` — sqlite3 CLI по умолчанию busy_timeout=0, и если в
# момент бэкапа scan-writer держит lock, backup падает с SQLITE_BUSY.
# 30с буфера хватает на любую запись пакета.
sqlite3 -cmd ".timeout 30000" "$DB" ".backup $TARGET"

# Бэкап, который нельзя восстановить, хуже отсутствующего (ложное чувство
# защищённости): ловим коррупцию ДО gzip, пока не затёрли ничего полезного.
CHECK=$(sqlite3 "$TARGET" "PRAGMA quick_check;")
if [ "$CHECK" != "ok" ]; then
  echo "ERROR: quick_check failed for $TARGET: $CHECK" >&2
  exit 1
fi

# gzip даёт ~10x compression для SQLite (много текста, повторов).
gzip -f "$TARGET"
gzip -t "$TARGET.gz"

# Ротация: удаляем gz-файлы старше KEEP_DAYS дней, а также осиротевшие
# несжатые .db (артефакты прогонов, упавших до введения trap'а). Имя файла
# включает дату+время — ротация полагается на mtime, но имя делает каталог
# человекочитаемым и позволяет ручной recovery-test НЕ клобберить ночной
# бэкап того же дня.
find "$DEST_DIR" -maxdepth 1 -name 'kvadstat-*.db.gz' -type f -mtime "+${KEEP_DAYS}" -delete
find "$DEST_DIR" -maxdepth 1 -name 'kvadstat-*.db' -type f -mtime "+1" -delete

# Offsite-ярус: без него потеря машины = потеря всей истории цен
# (восстановить её повторным сканом невозможно). Сбой пуша — это сбой
# бэкапа (exit != 0 → OnFailure-алерт), а не warning в логе.
if [ -n "$OFFSITE_REMOTE" ]; then
  rclone copyto "$TARGET.gz" "$OFFSITE_REMOTE/$(basename "$TARGET.gz")" \
    --no-traverse
  echo "offsite push ok: $OFFSITE_REMOTE/$(basename "$TARGET.gz")"
fi

echo "backup done: $TARGET.gz ($(du -h "$TARGET.gz" | cut -f1))"
