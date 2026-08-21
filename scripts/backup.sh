#!/usr/bin/env bash
# Snapshots the SQLite cache (cache.sqlite3) into backups/<timestamp>/
# (sqlite_migration.md §X — SQLite is now the source of truth, Sheets is
# gone: a lost database is not "rebuilt by the next sync", it is data loss).
#
# Two formats per snapshot, both verified before anything is kept:
#   - cache.sqlite3.backup  — SQLite's online backup API (byte-for-byte,
#     fast restore)
#   - cache.sqlite3.dump.gz — `sqlite3 .dump | gzip` (a page-level corruption
#     in the binary copy can make it unrestorable; the SQL dump is plain
#     text and can be hand-repaired)
#
# Retention is grandfather-father-son, not just "keep N recent": the last
# $KEEP_DAYS daily snapshots, one per week for $KEEP_WEEKS beyond that, one
# per month for $KEEP_MONTHS beyond that.
#
# Off-box copy is mandatory (§X: "вынос копии за пределы VPS обязателен" —
# a disk failure or lost VPS access takes the local backups with it). Set
# RCLONE_REMOTE to an `rclone remotes`-configured destination
# (e.g. "b2:stalbot-backups") to enable it; the script fails loudly if it
# is unset, rather than silently skipping the one thing that survives a
# lost VPS.
#
# Usage: ./scripts/backup.sh [source_db] [backups_dir]
#   source_db:    path to cache.sqlite3       (default: ./data/cache.sqlite3)
#   backups_dir:  where timestamped backups go (default: ./backups)
#
# Env:
#   RCLONE_REMOTE   rclone destination, e.g. "b2:stalbot-backups" (required
#                   unless BACKUP_SKIP_REMOTE=1 — local dev/testing only)
#   KEEP_DAYS       daily snapshots to keep locally   (default: 7)
#   KEEP_WEEKS      weekly snapshots beyond that       (default: 4)
#   KEEP_MONTHS     monthly snapshots beyond that      (default: 12)
#   BACKUP_SKIP_REMOTE=1   skip the off-box copy (local dev/testing only —
#                   never set this in the production timer)
#
# Schedule via deploy/stalbot-backup.{service,timer} (systemd), not cron —
# the timer gets `journalctl` logging and `OnCalendar` semantics for free.

set -euo pipefail

SOURCE_DB="${1:-./data/cache.sqlite3}"
BACKUPS_DIR="${2:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"
KEEP_WEEKS="${KEEP_WEEKS:-4}"
KEEP_MONTHS="${KEEP_MONTHS:-12}"

for cmd in sqlite3 gzip; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "error: $cmd not found on PATH" >&2
        exit 1
    fi
done

if [ ! -f "$SOURCE_DB" ]; then
    echo "error: source database not found: $SOURCE_DB" >&2
    exit 1
fi

if [ "${BACKUP_SKIP_REMOTE:-0}" != "1" ]; then
    if ! command -v rclone >/dev/null 2>&1; then
        echo "error: rclone not found on PATH — off-box backup is mandatory (§X)." >&2
        echo "       set BACKUP_SKIP_REMOTE=1 only for local dev/testing." >&2
        exit 1
    fi
    if [ -z "${RCLONE_REMOTE:-}" ]; then
        echo "error: RCLONE_REMOTE is not set — off-box backup is mandatory (§X)." >&2
        echo "       set BACKUP_SKIP_REMOTE=1 only for local dev/testing." >&2
        exit 1
    fi
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest_dir="${BACKUPS_DIR}/${timestamp}"
mkdir -p "$dest_dir"

dest_backup="${dest_dir}/cache.sqlite3.backup"
dest_dump_gz="${dest_dir}/cache.sqlite3.dump.gz"

# `.backup` uses SQLite's online backup API: safe to run against a database
# the bot is actively reading/writing, no downtime required.
sqlite3 "$SOURCE_DB" ".backup '${dest_backup}'"
echo "wrote ${dest_backup}"

sqlite3 "$SOURCE_DB" ".dump" | gzip -9 > "$dest_dump_gz"
echo "wrote ${dest_dump_gz}"

# integrity_check on the COPY, not the source (§X): a check against the
# live file only proves the live file is fine, not that what got copied is.
check_result="$(sqlite3 "$dest_backup" "PRAGMA integrity_check;")"
if [ "$check_result" != "ok" ]; then
    echo "error: integrity_check on the fresh backup failed: ${check_result}" >&2
    echo "       ${dest_backup} is NOT trustworthy — investigate before relying on it." >&2
    exit 1
fi
echo "integrity_check on ${dest_backup}: ok"

if [ "${BACKUP_SKIP_REMOTE:-0}" != "1" ]; then
    echo "copying ${dest_dir} to ${RCLONE_REMOTE}/${timestamp}/ ..."
    rclone copy "$dest_dir" "${RCLONE_REMOTE}/${timestamp}/"
    # Verify the remote copy is actually readable, not just "upload didn't
    # error" — rclone check compares size+hash between local and remote.
    rclone check "$dest_dir" "${RCLONE_REMOTE}/${timestamp}/"
    echo "verified remote copy at ${RCLONE_REMOTE}/${timestamp}/"
else
    echo "warning: BACKUP_SKIP_REMOTE=1 — no off-box copy made. Do not run this way in production." >&2
fi

# --- retention: grandfather-father-son ---
# Every backup dir is named by its UTC timestamp (sorts lexically = chronologically).
mapfile -t all_backups < <(find "$BACKUPS_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort)

keep=()
# Daily: the most recent $KEEP_DAYS, unconditionally.
daily_start=$((${#all_backups[@]} - KEEP_DAYS))
[ "$daily_start" -lt 0 ] && daily_start=0
for ((i = daily_start; i < ${#all_backups[@]}; i++)); do
    keep+=("${all_backups[$i]}")
done

# Weekly: for backups older than the daily window, keep the oldest one seen
# per ISO week.
declare -A seen_week=()
declare -A seen_month=()
weekly_count=0
monthly_count=0
for ((i = daily_start - 1; i >= 0; i--)); do
    dir="${all_backups[$i]}"
    name="$(basename "$dir")"
    day="${name:0:8}" # YYYYMMDD
    week_key="$(date -u -d "${day}" +%G-W%V 2>/dev/null || date -u -jf '%Y%m%d' "${day}" +%G-W%V)"
    month_key="${day:0:6}" # YYYYMM

    if [ -z "${seen_week[$week_key]:-}" ] && [ "$weekly_count" -lt "$KEEP_WEEKS" ]; then
        seen_week[$week_key]=1
        weekly_count=$((weekly_count + 1))
        keep+=("$dir")
        continue
    fi
    if [ -z "${seen_month[$month_key]:-}" ] && [ "$monthly_count" -lt "$KEEP_MONTHS" ]; then
        seen_month[$month_key]=1
        monthly_count=$((monthly_count + 1))
        keep+=("$dir")
    fi
done

for dir in "${all_backups[@]}"; do
    keep_this=0
    for k in "${keep[@]:-}"; do
        [ "$k" = "$dir" ] && keep_this=1 && break
    done
    if [ "$keep_this" -eq 0 ]; then
        echo "pruning old backup: $dir"
        rm -rf -- "$dir"
    fi
done

echo "done."
