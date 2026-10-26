#!/usr/bin/env bash
# Backs up the database and the uploaded files together, with every report's
# totals and the hashes of all three (ENG-01 7.2).
#
#   scripts/backup.sh            writes backups/YYYY-MM-DD_HHMMSS/
#   scripts/backup.sh <folder>   writes <folder>/YYYY-MM-DD_HHMMSS/ instead,
#                                which only the tests use
#
# Run it in Bash (Git Bash on Windows) with the database running. It acts on
# the Compose project docker compose would use, or the one
# COMPOSE_PROJECT_NAME names.
set -euo pipefail
# Git Bash turns arguments such as /uploads into Windows paths before they
# reach docker. Paths here are inside the containers, so it mustn't.
export MSYS_NO_PATHCONV=1

parent="${1:-backups}"
cd "$(dirname "$0")/.."

if ! docker compose ps --status running --services | grep -qx db; then
    echo "The database isn't running. Start it with: docker compose up -d" >&2
    exit 1
fi

target="$parent/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$parent"
mkdir "$target"

# app is stopped while the backup is taken, so no upload or approval lands
# between its parts, and started again however the backup ends. A backup
# that fails leaves no folder behind that could be mistaken for a whole one.
app_was_running=no
if docker compose ps --status running --services | grep -qx app; then
    app_was_running=yes
fi
finish() {
    status=$?
    if [ "$status" -ne 0 ]; then
        rm -f "$target/db.dump" "$target/uploads.tar" "$target/totals.json" \
            "$target/SHA256SUMS"
        rmdir "$target"
        echo "The backup failed, so nothing was kept." >&2
    fi
    if [ "$app_was_running" = yes ]; then
        docker compose start app >/dev/null
    fi
}
trap finish EXIT
if [ "$app_was_running" = yes ]; then
    docker compose stop app >/dev/null
fi

echo "Backing up the database..."
# As ids_owner, over the db container's own socket, which needs no password.
docker compose exec -T db sh -c \
    'pg_dump --username=ids_owner --dbname="$DB_NAME" --format=custom' \
    > "$target/db.dump"

echo "Backing up the uploaded files..."
docker compose run --rm --no-deps -T --entrypoint tar app -C /uploads -cf - . \
    > "$target/uploads.tar"

echo "Recording every report's totals..."
docker compose run --rm --no-deps -T app python manage.py report_totals \
    > "$target/totals.json"

(cd "$target" && sha256sum db.dump uploads.tar totals.json > SHA256SUMS)
echo "Backup written to $target"
