#!/bin/bash
# Запускается на Docker-хосте, не внутри контейнера приложения.
set -euo pipefail
umask 077
readonly BACKUP_ROOT="${BACKUP_ROOT:-/var/lib/tennison/backups}"
readonly DB_CONTAINER="${DB_CONTAINER:-tennisfan-site-rthluy-db-1}"
mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
exec 9>"$BACKUP_ROOT/.backup.lock"
flock -n 9 || exit 75
readonly STAMP="$(date -u +%Y%m%dT%H%M%S%N)"
readonly ARCHIVE="$BACKUP_ROOT/postgres-$STAMP.dump"
readonly PARTIAL="$ARCHIVE.partial"
trap 'if [[ -f "$PARTIAL" ]]; then unlink "$PARTIAL"; fi' EXIT
# Учётные данные берутся только внутри существующего контейнера БД.
docker exec "$DB_CONTAINER" sh -ec \
  'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$PARTIAL"
[[ -s "$PARTIAL" ]]
docker exec -i "$DB_CONTAINER" pg_restore --list < "$PARTIAL" > /dev/null
mv -n "$PARTIAL" "$ARCHIVE"
logger -t tennison-backup "PostgreSQL archive verified: $ARCHIVE"
