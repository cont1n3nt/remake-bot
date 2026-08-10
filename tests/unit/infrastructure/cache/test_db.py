"""Tests for `stalbot.infrastructure.cache.db.CacheDb` (§X, Э2)."""

from pathlib import Path

import aiosqlite
import pytest

from stalbot.infrastructure.cache.db import CacheDb, transaction
from stalbot.infrastructure.cache.migrations import current_version, discover_migrations


async def test_connect_creates_parent_directory_and_file(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "cache.sqlite3"
    db = CacheDb(db_path)

    await db.connect()

    assert db_path.exists()
    await db.close()


async def test_connect_applies_migrations_and_sets_user_version(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()

    version = await current_version(conn)

    assert version == discover_migrations()[-1].version
    await db.close()


async def test_connect_creates_every_table(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()

    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {row["name"] async for row in cursor}

    assert tables >= {
        "items",
        "users",
        "transactions",
        "progression_state",
        "ticket_sessions",
        "boost_order_lines",
        "screenshot_analyses",
        "sync_meta",
        "write_idempotency",
    }
    await db.close()


async def test_connect_is_idempotent_across_repeated_calls(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    first = await db.connect()
    second = await db.connect()

    assert first is second
    await db.close()


async def test_reconnecting_to_an_existing_file_keeps_the_same_user_version(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite3"
    first_db = CacheDb(db_path)
    first_conn = await first_db.connect()
    first_version = await current_version(first_conn)
    await first_db.close()

    second_db = CacheDb(db_path)
    second_conn = await second_db.connect()

    assert await current_version(second_conn) == first_version
    await second_db.close()


async def test_close_without_connect_is_a_no_op(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    await db.close()  # must not raise


async def test_connect_sets_durability_pragmas(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()

    async def pragma(name: str) -> object:
        cursor = await conn.execute(f"PRAGMA {name}")
        row = await cursor.fetchone()
        assert row is not None
        return row[0]

    assert await pragma("journal_mode") == "wal"
    assert await pragma("synchronous") == 2  # FULL (SQLite's numbering: OFF=0,NORMAL=1,FULL=2)
    assert await pragma("foreign_keys") == 1
    assert await pragma("busy_timeout") == 5000
    await db.close()


async def test_close_checkpoints_the_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    db = CacheDb(db_path)
    conn = await db.connect()
    async with transaction(conn):
        await conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('probe', 'x') "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    await db.close()

    wal_path = db_path.with_name(db_path.name + "-wal")
    # TRUNCATE checkpointing shrinks -wal to 0 bytes rather than deleting it
    # outright — either is an acceptable "nothing left to replay" state.
    assert not wal_path.exists() or wal_path.stat().st_size == 0


async def test_quick_check_rejects_a_corrupt_file(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    db_path.write_bytes(b"not a sqlite database, just garbage bytes" * 100)
    db = CacheDb(db_path)

    with pytest.raises(RuntimeError, match="quick_check"):
        await db.connect()


async def test_transaction_before_connect_raises() -> None:
    db = CacheDb(Path("unused.sqlite3"))
    with pytest.raises(RuntimeError, match="before connect"):
        db.transaction()


async def test_transaction_commits_on_success(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()

    async with db.transaction():
        await conn.execute("INSERT INTO sync_meta (key, value) VALUES ('probe', 'committed')")

    cursor = await conn.execute("SELECT value FROM sync_meta WHERE key = 'probe'")
    row = await cursor.fetchone()
    assert row is not None
    assert row["value"] == "committed"
    await db.close()


async def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()

    with pytest.raises(ValueError, match="boom"):
        async with db.transaction():
            await conn.execute("INSERT INTO sync_meta (key, value) VALUES ('probe', 'x')")
            raise ValueError("boom")

    cursor = await conn.execute("SELECT value FROM sync_meta WHERE key = 'probe'")
    assert await cursor.fetchone() is None
    await db.close()


async def test_transaction_module_function_works_on_a_bare_connection(tmp_path: Path) -> None:
    """Repositories call `db.transaction(connection)` directly, not via `CacheDb`."""
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()

    async with transaction(conn):
        await conn.execute("INSERT INTO sync_meta (key, value) VALUES ('probe', 'y')")

    cursor = await conn.execute("SELECT value FROM sync_meta WHERE key = 'probe'")
    row = await cursor.fetchone()
    assert row is not None and row["value"] == "y"
    await db.close()


async def test_migrating_an_already_populated_database_backs_it_up_first(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite3"
    conn = await aiosqlite.connect(db_path)
    await conn.execute("CREATE TABLE placeholder (x INTEGER)")
    await conn.commit()
    await conn.close()

    db = CacheDb(db_path)
    await db.connect()
    await db.close()

    backups = list(tmp_path.glob("cache.sqlite3.pre-migration-*.bak"))
    assert len(backups) == 1


async def test_migrating_a_brand_new_database_does_not_back_it_up(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    db = CacheDb(db_path)

    await db.connect()
    await db.close()

    backups = list(tmp_path.glob("cache.sqlite3.pre-migration-*.bak"))
    assert backups == []


async def test_adopts_a_legacy_sync_meta_schema_version_without_backup_noise(
    tmp_path: Path,
) -> None:
    """A pre-Э2 database (only the v4 baseline schema applied, a
    `sync_meta.schema_version` row, `PRAGMA user_version` never touched) is
    adopted in place — v4 is recorded without re-running 0004_baseline.sql —
    and then migrated forward through any newer migration (e.g. 0005) same
    as a fresh database would be."""
    db_path = tmp_path / "cache.sqlite3"
    conn = await aiosqlite.connect(db_path)
    repo_root = Path(__file__).resolve().parents[4]
    baseline_sql = (
        repo_root
        / "src"
        / "stalbot"
        / "infrastructure"
        / "cache"
        / "migrations"
        / "0004_baseline.sql"
    )
    await conn.executescript(baseline_sql.read_text(encoding="utf-8"))
    await conn.execute("INSERT INTO sync_meta (key, value) VALUES ('schema_version', '4')")
    await conn.commit()
    await conn.close()

    db = CacheDb(db_path)
    conn = await db.connect()

    assert await current_version(conn) == discover_migrations()[-1].version
    await db.close()
