#!/bin/sh
# Nightly backup of the Postgres database and the knowledge folder. Safe to run from cron: it moves to the repository folder itself.
#   BACKUP_DIR       where the files go (default /var/backups/deskfern)
#   BACKUP_COPY_TO   optional rsync target on ANOTHER machine, for example user@host:/backups/deskfern/ (a copy on the same disk is not a backup)
set -eu
cd "$(dirname "$0")/../.."
OUT="${BACKUP_DIR:-/var/backups/deskfern}"
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M)
DUMP="$OUT/deskfern-$STAMP.dump"
KNOW="$OUT/knowledge-$STAMP.tar"
TMP_DUMP="$DUMP.partial"
TMP_KNOW="$KNOW.partial"
trap 'rm -f -- "$TMP_DUMP" "$TMP_KNOW"' EXIT
# Write to a partial file first, so a failed dump never counts as a backup and never pushes a good one out of the rotation.
docker compose exec -T db pg_dump -U deskfern -Fc deskfern > "$TMP_DUMP"
if [ ! -s "$TMP_DUMP" ]; then
  echo "backup failed: the database dump is empty" >&2
  exit 1
fi
mv -- "$TMP_DUMP" "$DUMP"
# The knowledge volume holds the files you put there. It may be empty on a desk that only pastes pages, so a failure here is not fatal.
docker compose exec -T api tar -C /data -cf - knowledge > "$TMP_KNOW" || true
if [ -s "$TMP_KNOW" ]; then mv -- "$TMP_KNOW" "$KNOW"; fi
# keep the newest 14 of each
ls -1t "$OUT"/deskfern-*.dump | tail -n +15 | while read -r old; do rm -f -- "$old"; done
ls -1t "$OUT"/knowledge-*.tar 2>/dev/null | tail -n +15 | while read -r old; do rm -f -- "$old"; done
if [ -n "${BACKUP_COPY_TO:-}" ]; then
  rsync -a "$OUT"/ "$BACKUP_COPY_TO"
  echo "copied to $BACKUP_COPY_TO"
fi
echo "wrote $DUMP"
