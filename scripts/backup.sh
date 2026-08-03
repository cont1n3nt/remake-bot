#!/usr/bin/env bash
# Snapshots the SQLite cache (cache.sqlite3) into backups/<timestamp>/.
#
# The Google Sheet remains the source of truth — a lost SQLite cache is
# rebuilt by a full sync on the next start (M2). This backup exists so an
# operator doesn't have to wait for that resync, and so `screenshot_analyses`
# (the OCR training dataset, PLAN.md §11.8) and the last `/del_item`
# pre-mutation snapshot (PLAN.md §7.5, stored in `sync_meta`) survive a disk
# failure between resyncs.
#
# Usage: ./scripts/backup.sh [source_db] [backups_dir] [keep]
#   source_db:    path to cache.sqlite3            (default: ./data/cache.sqlite3)
#   backups_dir:  where timestamped backups go      (default: ./backups)
#   keep:         how many recent backups to retain (default: 14)
#
# Schedule this with cron or a systemd timer — the project has no built-in
# scheduler (README.md §8).

set -euo pipefail

SOURCE_DB="${1:-./data/cache.sqlite3}"
BACKUPS_DIR="${2:-./backups}"
KEEP="${3:-14}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "error: sqlite3 CLI not found on PATH" >&2
    exit 1
fi

if [ ! -f "$SOURCE_DB" ]; then
    echo "error: source database not found: $SOURCE_DB" >&2
    exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest_dir="${BACKUPS_DIR}/${timestamp}"
mkdir -p "$dest_dir"

dest_db="${dest_dir}/cache.sqlite3"

# `.backup` uses SQLite's online backup API: safe to run against a database
# the bot is actively reading/writing, no downtime required.
sqlite3 "$SOURCE_DB" ".backup '${dest_db}'"

echo "backed up ${SOURCE_DB} -> ${dest_db}"

# Prune old backups beyond $KEEP, oldest first.
mapfile -t existing < <(find "$BACKUPS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)
count=${#existing[@]}
if [ "$count" -gt "$KEEP" ]; then
    to_remove=$((count - KEEP))
    for ((i = 0; i < to_remove; i++)); do
        echo "pruning old backup: ${existing[$i]}"
        rm -rf -- "${existing[$i]}"
    done
fi
