"""`schema.sql` is a generated dump, not authoritative (§X, Э2) — this test
is what keeps that true: running every migration from scratch must produce
the exact same `sqlite_master` as applying `schema.sql` directly.
"""

from pathlib import Path

import aiosqlite

from stalbot.infrastructure.cache.migrations import run_migrations

_SCHEMA_SQL_PATH = Path(__file__).resolve().parents[4] / "src" / "stalbot" / "infrastructure"
_SCHEMA_SQL_PATH = _SCHEMA_SQL_PATH / "cache" / "schema.sql"


async def _sqlite_master_snapshot(conn: aiosqlite.Connection) -> set[tuple[str, str, str]]:
    cursor = await conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return {(row["type"], row["name"], row["sql"]) async for row in cursor}


async def test_migrations_from_scratch_reproduce_schema_sql() -> None:
    from_migrations = await aiosqlite.connect(":memory:")
    from_migrations.row_factory = aiosqlite.Row
    await run_migrations(from_migrations)

    from_schema_sql = await aiosqlite.connect(":memory:")
    from_schema_sql.row_factory = aiosqlite.Row
    await from_schema_sql.executescript(_SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    await from_schema_sql.commit()

    assert await _sqlite_master_snapshot(from_migrations) == await _sqlite_master_snapshot(
        from_schema_sql
    )

    await from_migrations.close()
    await from_schema_sql.close()
