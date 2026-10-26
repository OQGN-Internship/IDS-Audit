#!/usr/bin/env bash
# Restores a backup made by backup.sh: the database and the uploaded files.
# Then it checks every report's totals and every upload's file against the
# backup, and records the restore in the audit log (ENG-01 7.2).
#
#   scripts/restore.sh backups/<folder> [--no-safety-backup]
#
# It first backs up what it's about to replace, into the backup's parent
# folder, so no audit event is lost. --no-safety-backup skips that, for a
# database too damaged to back up.
set -euo pipefail
# Git Bash turns arguments such as /uploads into Windows paths before they
# reach docker. Paths here are inside the containers, so it mustn't.
export MSYS_NO_PATHCONV=1

usage() {
    echo "Usage: scripts/restore.sh backups/<folder> [--no-safety-backup]" >&2
    exit 2
}
[ $# -ge 1 ] && [ $# -le 2 ] || usage
safety=yes
if [ $# -eq 2 ]; then
    [ "$2" = "--no-safety-backup" ] || usage
    safety=no
fi
folder="$(cd "$1" 2>/dev/null && pwd)" || {
    echo "There's no backup folder $1." >&2
    exit 1
}
name="$(basename "$folder")"
cd "$(dirname "$0")/.."

echo "1. Checking the backup's files against SHA256SUMS..."
for file in db.dump uploads.tar totals.json; do
    if ! grep -q " \*\?$file\$" "$folder/SHA256SUMS" 2>/dev/null; then
        echo "SHA256SUMS doesn't list $file, so nothing was restored." >&2
        exit 1
    fi
done
if ! (cd "$folder" && sha256sum --check --quiet SHA256SUMS); then
    echo "The backup's files don't match SHA256SUMS, so nothing was restored." >&2
    exit 1
fi

if [ "$safety" = yes ]; then
    echo "2. Backing up what the restore replaces..."
    "$BASH" scripts/backup.sh "$(dirname "$folder")"
fi

echo "3. Stopping the application..."
docker compose stop app >/dev/null

echo "4. Restoring the database..."
# In one transaction, so a restore that fails leaves the database as it was.
docker compose exec -T db sh -c \
    'pg_restore --username=ids_owner --dbname="$DB_NAME" --clean --if-exists --single-transaction --exit-on-error' \
    < "$folder/db.dump"

echo "5. Restoring the uploaded files..."
docker compose run --rm --no-deps -T --entrypoint tar app -C /uploads -xf - \
    < "$folder/uploads.tar"

echo "6. Starting the application, after migrate applies the grants..."
docker compose up -d

echo "7. Comparing every total and every file with the backup..."
totals="$(mktemp)"
trap 'rm -f "$totals"' EXIT
docker compose exec -T app python manage.py report_totals > "$totals"
totals_match=yes
cmp -s "$totals" "$folder/totals.json" || totals_match=no
files_match=yes
docker compose exec -T app python manage.py check_uploads || files_match=no

echo "8. Recording the restore in the audit log..."
docker compose exec -T app python manage.py record_restore "$name" \
    --totals-match "$totals_match" --files-match "$files_match"

if [ "$totals_match" = yes ] && [ "$files_match" = yes ]; then
    echo "Restored $name: every total and every file matches the backup."
    exit 0
fi
if [ "$totals_match" = no ]; then
    echo "Restored $name, but the totals differ from its totals.json." >&2
fi
if [ "$files_match" = no ]; then
    echo "Restored $name, but uploaded files are missing or changed, as listed above." >&2
fi
exit 1
