"""Numbered SQL migration runner (sqlite_migration.md §X, Э2).

Each migration is a file in this directory named `NNNN_description.sql`
(`NNNN` a zero-padded, strictly increasing version number). Applying a
migration and recording that it ran happen in one SQLite transaction —
`PRAGMA user_version` is a database-header field, not a table row, so it is
written and rolled back exactly like any other statement in that
transaction (unlike the old `sync_meta.schema_version` row it replaces,
which could silently drift from the tables it described if a migration's
DDL half-applied and its bookkeeping half didn't).

`0004_baseline.sql` starts numbering at 4, not 1: it collapses the four
schema versions that existed before this migration system did (there was
no deployed pre-v1.0 data to replay incrementally through). Databases
already at that state, tracked the old way via a `sync_meta` row, are
adopted in place — see `_adopt_legacy_schema_version` — not re-migrated.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR: Path = Path(__file__).parent
_FILENAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_LEGACY_SCHEMA_VERSION_KEY = "schema_version"


class DowngradeError(RuntimeError):
    """Raised when the database is newer than the running code's migrations.

    Silently proceeding here is how a rolled-back binary quietly corrupts
    data (sqlite_migration.md §X): it would see columns/tables it doesn't
    know about, not error, and start writing incompletely.
    """


class MigrationDiscoveryError(RuntimeError):
    """Raised for a malformed or duplicated migration filename."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One versioned, idempotent-or-not DDL step."""

    version: int
    name: str
    sql: str


def discover_migrations(directory: Path | None = None) -> tuple[Migration, ...]:
    """Load every `NNNN_description.sql` file in `directory`, sorted by version.

    Args:
        directory: Where to look; defaults to this package's own directory.

    Returns:
        Migrations in ascending version order.

    Raises:
        MigrationDiscoveryError: A filename doesn't match `NNNN_name.sql`,
            or two files share a version number.
    """
    directory = directory if directory is not None else _MIGRATIONS_DIR
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationDiscoveryError(
                f"migration filename must look like 'NNNN_description.sql', got {path.name!r}"
            )
        migrations.append(
            Migration(
                version=int(match.group(1)),
                name=path.stem,
                sql=path.read_text(encoding="utf-8"),
            )
        )
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationDiscoveryError(f"duplicate migration version numbers: {versions}")
    return tuple(migrations)


async def current_version(connection: aiosqlite.Connection) -> int:
    """Return the database's `PRAGMA user_version` — the applied schema version.

    Public for `/healthcheck` (§VIII: `dto/health_status.py` grows a
    `schema_version` field once Sheets-era fields are retired) and tests.
    Does **not** adopt a legacy `sync_meta` row — call `run_migrations`
    (or `pending_migrations`) first if that matters for the caller.
    """
    cursor = await connection.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    assert row is not None  # noqa: S101 - PRAGMA user_version always returns one row
    return int(row[0])


_read_user_version = current_version


async def _adopt_legacy_schema_version(connection: aiosqlite.Connection, current: int) -> int:
    """Adopt a pre-migration-system database's `sync_meta.schema_version`.

    Only runs when `PRAGMA user_version` is still at its SQLite default
    (0) and a `sync_meta` row says otherwise — a database created before
    this migration system existed. `0004_baseline.sql` is exactly that
    schema, so there is nothing to *apply*, only to record.
    """
    if current != 0:
        return current
    cursor = await connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sync_meta'"
    )
    if await cursor.fetchone() is None:
        return current
    cursor = await connection.execute(
        "SELECT value FROM sync_meta WHERE key = ?", (_LEGACY_SCHEMA_VERSION_KEY,)
    )
    row = await cursor.fetchone()
    if row is None:
        return current
    legacy_version = int(row["value"])
    logger.info(
        "adopting legacy sync_meta.schema_version=%d as PRAGMA user_version", legacy_version
    )
    await connection.execute(f"PRAGMA user_version = {legacy_version}")
    await connection.commit()
    return legacy_version


async def pending_migrations(
    connection: aiosqlite.Connection, *, migrations: tuple[Migration, ...] | None = None
) -> tuple[Migration, ...]:
    """Return the migrations that `run_migrations` would apply, without applying them."""
    migrations = migrations if migrations is not None else discover_migrations()
    current = await _read_user_version(connection)
    current = await _adopt_legacy_schema_version(connection, current)
    return tuple(m for m in migrations if m.version > current)


async def run_migrations(
    connection: aiosqlite.Connection, *, migrations: tuple[Migration, ...] | None = None
) -> int:
    """Apply every not-yet-applied migration, each in its own transaction.

    Args:
        connection: An open connection. Caller owns PRAGMA setup
            (journal_mode, synchronous, ...) before calling this.
        migrations: Override for tests; defaults to `discover_migrations()`.

    Returns:
        The resulting `PRAGMA user_version`.

    Raises:
        DowngradeError: The database's version is newer than any migration
            this build knows about.
    """
    migrations = migrations if migrations is not None else discover_migrations()
    current = await _read_user_version(connection)
    current = await _adopt_legacy_schema_version(connection, current)

    target = migrations[-1].version if migrations else current
    if current > target:
        raise DowngradeError(
            f"database is at schema version {current}, but this build only knows migrations "
            f"up to {target} — refusing to start (a rolled-back binary against a newer database "
            "is exactly the silent-corruption scenario this guard exists to catch)"
        )

    for migration in migrations:
        if migration.version <= current:
            continue
        logger.info("applying migration %04d_%s", migration.version, migration.name)
        script = (
            f"BEGIN IMMEDIATE;\n{migration.sql}\n"
            f"PRAGMA user_version = {migration.version};\nCOMMIT;\n"
        )
        await connection.executescript(script)

    return target
