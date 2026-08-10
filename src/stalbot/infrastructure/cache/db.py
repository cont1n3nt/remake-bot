"""SQLite cache connection, PRAGMA/durability setup, and migrations (§X, Э2).

`connect()` is the only place that opens the file, so it's the one place
that has to get durability right: WAL for concurrent readers during a
`.backup`, `synchronous = FULL` so a crash loses a transaction rather than
corrupting the file, `busy_timeout` so `backup.sh`/an admin `sqlite3`
session don't immediately collide with the bot, and `quick_check` so a
corrupt file fails loudly at startup instead of silently serving garbage.
"""

import asyncio
import logging
import shutil
import weakref
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import aiosqlite

from stalbot.infrastructure.cache.migrations import pending_migrations, run_migrations

logger = logging.getLogger(__name__)

#: milliseconds a writer waits for a lock before raising `SQLITE_BUSY` —
#: `backup.sh`'s `.backup` and an admin `sqlite3` session are the other
#: concurrent clients this guards against (§X).
_BUSY_TIMEOUT_MS: Final = 5_000


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
        """Open the connection on first call, set durability PRAGMAs, and migrate.

        Returns:
            The shared connection, with `row_factory` set to `aiosqlite.Row`
            so repositories can access columns by name.
        """
        if self._connection is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self._path)
            connection.row_factory = aiosqlite.Row
            # Integrity first: WAL mode itself writes to the file, and a
            # genuinely corrupt file can fail that write before ever
            # reaching quick_check's own result row.
            await self._check_integrity(connection)
            await self._configure_pragmas(connection)
            self._connection = connection
            await self._migrate(connection)
        return self._connection

    async def close(self) -> None:
        """Checkpoint the WAL and close the connection, if one is open.

        `wal_checkpoint(TRUNCATE)` folds `-wal`/`-shm` back into the main
        file and removes them — a stopped bot leaves one clean file behind,
        not a three-file set a backup taken while stopped could miss (§X).
        """
        if self._connection is not None:
            await self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await self._connection.close()
            self._connection = None

    def transaction(self) -> "Transaction":
        """Open an explicit `BEGIN IMMEDIATE` transaction as an async context manager.

        A thin convenience wrapper around the module-level `transaction()`
        for callers that only hold a `CacheDb`, not a raw connection — e.g.
        a future service composing several repositories' writes into one
        logical operation (Э7's `TransactionService.register()`: `deals` +
        `players` + `player_progression` + `write_idempotency` in one go).

        Raises:
            RuntimeError: `connect()` has not been called yet.
        """
        if self._connection is None:
            raise RuntimeError("CacheDb.transaction() called before connect()")
        return transaction(self._connection)

    async def _configure_pragmas(self, connection: aiosqlite.Connection) -> None:
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA synchronous = FULL")
        await connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")

    async def _check_integrity(self, connection: aiosqlite.Connection) -> None:
        try:
            cursor = await connection.execute("PRAGMA quick_check")
            row = await cursor.fetchone()
        except aiosqlite.Error as exc:
            # A file that isn't a SQLite database at all (e.g. truncated,
            # overwritten with garbage) fails right here rather than
            # returning a quick_check result row.
            raise RuntimeError(
                f"cache database at {self._path} could not be read: {exc} "
                "— refusing to start against a possibly corrupt file (§X: fail loud, don't "
                "serve garbage). Restore from backup (scripts/restore.sh)."
            ) from exc
        result = row[0] if row is not None else None
        if result != "ok":
            raise RuntimeError(
                f"cache database at {self._path} failed PRAGMA quick_check: {result!r} "
                "— refusing to start against a possibly corrupt file (§X: fail loud, don't "
                "serve garbage). Restore from backup (scripts/restore.sh)."
            )

    async def _migrate(self, connection: aiosqlite.Connection) -> None:
        if await pending_migrations(connection) and await self._has_any_tables(connection):
            self._backup_before_migration()
        await run_migrations(connection)

    async def _has_any_tables(self, connection: aiosqlite.Connection) -> bool:
        """`False` for a just-created, empty database — nothing worth backing up yet."""
        cursor = await connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'")
        row = await cursor.fetchone()
        return row is not None and int(row[0]) > 0

    def _backup_before_migration(self) -> None:
        """Copy the (already WAL-mode) database file before altering it.

        A best-effort local safety net, not a substitute for `backup.sh`'s
        off-box copies — it lives next to the live file and would be lost
        with it in a disk failure.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self._path.with_name(f"{self._path.name}.pre-migration-{timestamp}.bak")
        shutil.copy2(self._path, backup_path)
        logger.info(
            "backed up %s to %s before applying pending migrations", self._path, backup_path
        )


#: One lock per connection, keyed by identity so every `transaction(conn)`
#: call for the *same* connection shares one gate. Needed because two
#: coroutines both awaiting `conn.execute("BEGIN IMMEDIATE")` on one
#: `aiosqlite.Connection` do not queue at the SQLite level the way two
#: separate connections would — `aiosqlite`/`sqlite3` tracks "already in a
#: transaction" per Python object and raises immediately rather than
#: blocking, so without this lock a second coroutine's `BEGIN` while the
#: first is still open fails with `OperationalError: cannot start a
#: transaction within a transaction` instead of waiting its turn. Every
#: repository shares the one connection `CacheDb.connect()` returns (see
#: `bot.py`'s `setup_hook`), so this is exactly the case in practice, not a
#: hypothetical.
_locks: "weakref.WeakKeyDictionary[aiosqlite.Connection, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _lock_for(connection: aiosqlite.Connection) -> asyncio.Lock:
    lock = _locks.get(connection)
    if lock is None:
        lock = asyncio.Lock()
        _locks[connection] = lock
    return lock


class Transaction:
    """`async with`-able `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` block.

    `IMMEDIATE` (not the default `DEFERRED`) acquires the write lock up
    front, so a second concurrent writer fails fast with `SQLITE_BUSY` —
    bounded by `busy_timeout` — instead of two transactions racing to
    upgrade a shared read lock later and one losing after doing partial
    work. Serialized per connection by `_lock_for` (see its docstring).
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap the connection this block will run `BEGIN IMMEDIATE` on."""
        self._connection = connection
        self._lock = _lock_for(connection)

    async def __aenter__(self) -> aiosqlite.Connection:
        """Acquire this connection's transaction lock and start the transaction."""
        await self._lock.acquire()
        await self._connection.execute("BEGIN IMMEDIATE")
        return self._connection

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Commit (or roll back, if the block raised), then release the lock."""
        try:
            if exc_type is None:
                await self._connection.commit()
            else:
                await self._connection.rollback()
        finally:
            self._lock.release()


def transaction(connection: aiosqlite.Connection) -> Transaction:
    """Open an explicit `BEGIN IMMEDIATE` transaction on an existing connection.

    Repositories use this in place of a bare `execute(...); commit()` —
    every write call site in `infrastructure/cache/repositories/` shares
    the single connection `CacheDb.connect()` returns (see `bot.py`'s
    `setup_hook`), so this same primitive composes across repositories too
    once a caller needs that (Э7). Concurrent callers on the same
    connection queue for the lock rather than colliding — see `_lock_for`.

    Args:
        connection: An open connection, typically from `CacheDb.connect()`.
    """
    return Transaction(connection)
