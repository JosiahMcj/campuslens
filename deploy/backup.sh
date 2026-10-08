#!/bin/sh
# Scheduled backup with rotation: snapshot the database and dataset
# files via `make backup` (SQLite backup API + sha256 manifest, verified
# after the write), then keep only the newest CABINET_BACKUP_KEEP
# (default 14) snapshots in the backups directory (<db dir>/backups, i.e.
# var/backups/ by default).
#
# Driven by deploy/launchd/com.campuslens.app-backup.plist on macOS or
# deploy/systemd/cabinet-backup.timer on Linux — or by hand:
#   deploy/backup.sh
#
# Only directories whose names match the backup timestamp shape
# (YYYYMMDDTHHMMSSZ) are rotated; anything else there — including the
# *.pre-restore-* asides `make restore` writes — is left alone.

set -eu

cd "$(dirname "$0")/.."

# Load the same env file the app uses (CABINET_LOCAL_ENV, as set by the
# launchd plist; systemd's EnvironmentFile has already expanded it) so the
# backup sees the same CABINET_DB — otherwise it would snapshot the default
# var/cabinet.db while the app writes elsewhere.
if [ -n "${CABINET_LOCAL_ENV:-}" ] && [ -f "$CABINET_LOCAL_ENV" ]; then
    set -a
    . "$CABINET_LOCAL_ENV"
    set +a
fi

make backup

keep=${CABINET_BACKUP_KEEP:-14}
# `make backup` writes to <db dir>/backups (cabinet.backup's default), so
# rotate the same directory — not always var/backups.
db=${CABINET_DB:-var/cabinet.db}
backups_dir=$(dirname "$db")/backups
[ -d "$backups_dir" ] || exit 0

# Timestamps are UTC and fixed-width, so a plain sort is the age order; a
# same-second collision is impossible (`make backup` refuses an existing
# directory).
count=$(ls -1 "$backups_dir" | grep -cE '^[0-9]{8}T[0-9]{6}Z$' || true)
excess=$((count - keep))
if [ "$excess" -gt 0 ]; then
    ls -1 "$backups_dir" \
        | grep -E '^[0-9]{8}T[0-9]{6}Z$' \
        | sort \
        | head -n "$excess" \
        | while read -r name; do
            rm -rf "${backups_dir:?}/$name"
            echo "backup: rotated out $backups_dir/$name (keeping the newest $keep)"
        done
fi
