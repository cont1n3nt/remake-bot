#!/usr/bin/env bash
# Restore a backup written by scripts/backup.sh, verify it, and (by default)
# stop there — this is a drill/inspection tool first, "replace the live
# database" second (sqlite_migration.md §X: "проведённая учебная тревога").
#
# Usage:
#   ./scripts/restore.sh <backup_dir> [--apply <target_db>]
#
#   backup_dir:  a timestamped directory from backups/, e.g.
#                backups/20260810T120000Z
#   --apply:     after verification passes, replace <target_db> with the
#                restored file (stops the bot first if it's running under
#                systemd — see below). Omit this to just run the drill:
#                restore into a scratch directory, verify, and stop.
#
# What "verify" means here (§X's restore checklist):
#   1. restore into a fresh scratch location (never overwrite anything live
#      before verification passes)
#   2. PRAGMA integrity_check
#   3. row counts for every user table, printed for a human sanity check
#      against what the operator expects (row counts alone can't prove
#      correctness, only catch a badly truncated/wrong-era backup)
#   4. NOTE: once Э3 (`deals`/`player_progression`) and Э5 (`shelter_cost`)
#      exist, this script should additionally recompute both from the
#      restored `deals`/`recipes` and diff against the stored
#      `player_progression`/`shelter_cost` rows — the strongest possible
#      check, since it re-derives the numbers instead of trusting them.
#      Not implemented yet: those tables don't exist in this build.

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <backup_dir> [--apply <target_db>]" >&2
    exit 1
fi

BACKUP_DIR="$1"
shift
APPLY_TARGET=""
if [ "${1:-}" = "--apply" ]; then
    APPLY_TARGET="${2:?--apply requires a target db path}"
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "error: sqlite3 CLI not found on PATH" >&2
    exit 1
fi

BINARY_BACKUP="${BACKUP_DIR}/cache.sqlite3.backup"
DUMP_GZ="${BACKUP_DIR}/cache.sqlite3.dump.gz"

if [ ! -f "$BINARY_BACKUP" ] && [ ! -f "$DUMP_GZ" ]; then
    echo "error: neither ${BINARY_BACKUP} nor ${DUMP_GZ} exists" >&2
    exit 1
fi

scratch_dir="$(mktemp -d /tmp/stalbot-restore-XXXXXX)"
trap 'rm -rf -- "$scratch_dir"' EXIT
scratch_db="${scratch_dir}/cache.sqlite3"

if [ -f "$BINARY_BACKUP" ]; then
    echo "restoring from binary backup: ${BINARY_BACKUP}"
    cp "$BINARY_BACKUP" "$scratch_db"
else
    echo "binary backup missing, falling back to SQL dump: ${DUMP_GZ}"
    gunzip -c "$DUMP_GZ" | sqlite3 "$scratch_db"
fi

echo "--- 1. integrity_check ---"
check_result="$(sqlite3 "$scratch_db" "PRAGMA integrity_check;")"
echo "$check_result"
if [ "$check_result" != "ok" ]; then
    echo "error: restored database failed integrity_check — this backup is not usable." >&2
    exit 1
fi

echo "--- 2. row counts ---"
sqlite3 "$scratch_db" <<'SQL'
.headers on
.mode column
SELECT 'items' AS table_name, COUNT(*) AS rows FROM items
UNION ALL SELECT 'users', COUNT(*) FROM users
UNION ALL SELECT 'transactions', COUNT(*) FROM transactions
UNION ALL SELECT 'ticket_sessions', COUNT(*) FROM ticket_sessions
UNION ALL SELECT 'boost_order_lines', COUNT(*) FROM boost_order_lines
UNION ALL SELECT 'screenshot_analyses', COUNT(*) FROM screenshot_analyses
UNION ALL SELECT 'write_idempotency', COUNT(*) FROM write_idempotency;
SQL

echo "--- 3. schema version ---"
sqlite3 "$scratch_db" "PRAGMA user_version;"

echo
echo "Scratch copy verified at: $scratch_db (deleted when this script exits)"
echo "Manual next step: eyeball the row counts above against what you expect"
echo "(last known player/transaction count) before trusting this backup."

if [ -n "$APPLY_TARGET" ]; then
    echo
    echo "--apply requested: replacing ${APPLY_TARGET}"
    if systemctl is-active --quiet stalbot 2>/dev/null; then
        echo "stopping stalbot.service first..."
        sudo systemctl stop stalbot
    fi
    mkdir -p "$(dirname "$APPLY_TARGET")"
    if [ -f "$APPLY_TARGET" ]; then
        pre_replace_backup="${APPLY_TARGET}.pre-restore-$(date -u +%Y%m%dT%H%M%SZ).bak"
        cp "$APPLY_TARGET" "$pre_replace_backup"
        echo "existing ${APPLY_TARGET} saved to ${pre_replace_backup} before overwrite"
    fi
    cp "$scratch_db" "$APPLY_TARGET"
    echo "restored into ${APPLY_TARGET}. Start the bot manually when ready:"
    echo "  sudo systemctl start stalbot"
fi
