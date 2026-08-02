"""SQLite cache connection and schema migration (see PLAN.md §8.1).

`schema.sql` is written to be idempotent (`CREATE TABLE IF NOT EXISTS`), so
applying it on every startup is always safe; `sync_meta.schema_version`
records what actually ran, giving future milestones a place to detect and
run real migrations instead of just re-applying the same file.
"""

import logging
from pathlib import Path
from typing import Final

import aiosqlite

logger = logging.getLogger(__name__)

#: Bumped whenever `schema.sql` gains a column/table that old rows do not
#: have and a real migration (not just re-running the idempotent DDL) is
#: needed to backfill them.
SCHEMA_VERSION: Final = 1

_SCHEMA_PATH: Final = Path(__file__).with_name("schema.sql")
_SCHEMA_VERSION_KEY: Final = "schema_version"


class CacheDb:
    """Owns the single `aiosqlite` connection to the local cache database."""

    def __init__(self, path: Path) -> None:
        """Configure the database location without opening it yet.

        Args:
            path: Filesystem path to the SQLite file (`CACHE_DB_PATH`).
        """
        self._path = path
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        """Open the connection on first call and apply the schema.

        Returns:
            The shared connection, with `row_factory` set to `aiosqlite.Row`
            so repositories can access columns by name.
        """
        if self._connection is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self._path)
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            self._connection = connection
            await self._migrate()
        return self._connection

    async def close(self) -> None:
        """Close the connection, if one is open."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _migrate(self) -> None:
        assert self._connection is not None  # noqa: S101 - connect() just set it
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self._connection.executescript(schema_sql)

        cursor = await self._connection.execute(
            "SELECT value FROM sync_meta WHERE key = ?", (_SCHEMA_VERSION_KEY,)
        )
        row = await cursor.fetchone()
        if row is None:
            await self._connection.execute(
                "INSERT INTO sync_meta (key, value) VALUES (?, ?)",
                (_SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
            )
            await self._connection.commit()
        elif int(row["value"]) != SCHEMA_VERSION:
            logger.warning(
                "cache schema version mismatch: found %s, expected %s (no migration defined yet)",
                row["value"],
                SCHEMA_VERSION,
            )
