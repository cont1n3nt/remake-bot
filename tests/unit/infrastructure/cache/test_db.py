"""Tests for `stalbot.infrastructure.cache.db.CacheDb`."""

from pathlib import Path

from stalbot.infrastructure.cache.db import SCHEMA_VERSION, CacheDb


async def test_connect_creates_parent_directory_and_file(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "cache.sqlite3"
    db = CacheDb(db_path)

    await db.connect()

    assert db_path.exists()
    await db.close()


async def test_connect_applies_schema_and_records_version(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()

    cursor = await conn.execute("SELECT value FROM sync_meta WHERE key = 'schema_version'")
    row = await cursor.fetchone()

    assert row is not None
    assert int(row["value"]) == SCHEMA_VERSION
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
    }
    await db.close()


async def test_connect_is_idempotent_across_repeated_calls(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    first = await db.connect()
    second = await db.connect()

    assert first is second
    await db.close()


async def test_reconnecting_to_an_existing_file_does_not_duplicate_schema_version_row(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite3"
    first_db = CacheDb(db_path)
    await first_db.connect()
    await first_db.close()

    second_db = CacheDb(db_path)
    conn = await second_db.connect()
    cursor = await conn.execute("SELECT COUNT(*) AS n FROM sync_meta WHERE key = 'schema_version'")
    row = await cursor.fetchone()

    assert row is not None
    assert row["n"] == 1
    await second_db.close()


async def test_close_without_connect_is_a_no_op(tmp_path: Path) -> None:
    db = CacheDb(tmp_path / "cache.sqlite3")
    await db.close()  # must not raise
