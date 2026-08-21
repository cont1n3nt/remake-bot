"""Tests for `stalbot.infrastructure.cache.migrations` (§X, Э2)."""

from pathlib import Path

import aiosqlite
import pytest

from stalbot.infrastructure.cache.migrations import (
    DowngradeError,
    Migration,
    MigrationDiscoveryError,
    current_version,
    discover_migrations,
    pending_migrations,
    run_migrations,
)


async def _connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return conn


def test_discover_migrations_reads_the_real_package_directory() -> None:
    migrations = discover_migrations()

    assert len(migrations) >= 1
    assert migrations[0].version == 4
    assert migrations[0].name == "0004_baseline"
    versions = [m.version for m in migrations]
    assert versions == sorted(versions)


def test_discover_migrations_rejects_a_bad_filename(tmp_path: Path) -> None:
    (tmp_path / "not_numbered.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationDiscoveryError, match="NNNN_description"):
        discover_migrations(tmp_path)


def test_discover_migrations_rejects_duplicate_versions(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_b.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(MigrationDiscoveryError, match="duplicate"):
        discover_migrations(tmp_path)


async def test_run_migrations_applies_every_migration_in_order() -> None:
    conn = await _connection()
    migrations = (
        Migration(1, "0001_a", "CREATE TABLE a (x INTEGER);"),
        Migration(2, "0002_b", "CREATE TABLE b (y INTEGER);"),
    )

    result = await run_migrations(conn, migrations=migrations)

    assert result == 2
    assert await current_version(conn) == 2
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {row["name"] async for row in cursor}
    assert tables == {"a", "b"}
    await conn.close()


async def test_run_migrations_is_idempotent_on_a_second_call() -> None:
    conn = await _connection()
    migrations = (Migration(1, "0001_a", "CREATE TABLE a (x INTEGER);"),)

    await run_migrations(conn, migrations=migrations)
    # A second call must not try to re-run 0001 (which would fail: table
    # already exists and isn't declared IF NOT EXISTS here on purpose, to
    # prove skip-already-applied actually skips).
    result = await run_migrations(conn, migrations=migrations)

    assert result == 1
    await conn.close()


async def test_run_migrations_only_applies_versions_newer_than_current() -> None:
    conn = await _connection()
    first_batch = (Migration(1, "0001_a", "CREATE TABLE a (x INTEGER);"),)
    await run_migrations(conn, migrations=first_batch)

    second_batch = (
        *first_batch,
        Migration(2, "0002_b", "CREATE TABLE b (y INTEGER);"),
    )
    result = await run_migrations(conn, migrations=second_batch)

    assert result == 2
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {row["name"] async for row in cursor}
    assert tables == {"a", "b"}
    await conn.close()


async def test_run_migrations_rolls_back_a_failed_migration() -> None:
    conn = await _connection()
    migrations = (
        Migration(1, "0001_ok", "CREATE TABLE a (x INTEGER);"),
        Migration(2, "0002_broken", "CREATE TABLE a (this is not valid SQL"),
    )

    with pytest.raises(aiosqlite.Error):
        await run_migrations(conn, migrations=migrations)

    # Version 1 succeeded and must stick; version 2 must not be recorded as
    # applied even partially.
    assert await current_version(conn) == 1
    await conn.close()


async def test_run_migrations_raises_downgrade_error_when_db_is_newer() -> None:
    conn = await _connection()
    await run_migrations(conn, migrations=(Migration(5, "0005_future", "SELECT 1;"),))

    with pytest.raises(DowngradeError, match="version 5"):
        await run_migrations(conn, migrations=(Migration(4, "0004_baseline", "SELECT 1;"),))

    await conn.close()


async def test_pending_migrations_reports_only_unapplied_ones() -> None:
    conn = await _connection()
    migrations = (
        Migration(1, "0001_a", "CREATE TABLE a (x INTEGER);"),
        Migration(2, "0002_b", "CREATE TABLE b (y INTEGER);"),
    )
    await run_migrations(conn, migrations=(migrations[0],))

    pending = await pending_migrations(conn, migrations=migrations)

    assert pending == (migrations[1],)
    await conn.close()


async def test_adopts_legacy_sync_meta_schema_version_row() -> None:
    conn = await _connection()
    await conn.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    await conn.execute("INSERT INTO sync_meta (key, value) VALUES ('schema_version', '4')")
    await conn.commit()

    pending = await pending_migrations(
        conn, migrations=(Migration(4, "0004_baseline", "SELECT 1;"),)
    )

    assert pending == ()
    assert await current_version(conn) == 4
    await conn.close()


async def test_no_legacy_row_and_fresh_user_version_means_everything_is_pending() -> None:
    conn = await _connection()
    migrations = (Migration(4, "0004_baseline", "CREATE TABLE items (id INTEGER);"),)

    pending = await pending_migrations(conn, migrations=migrations)

    assert pending == migrations
    await conn.close()
