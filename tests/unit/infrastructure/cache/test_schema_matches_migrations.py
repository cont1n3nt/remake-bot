"""`schema.sql` is a generated dump, not authoritative (§X, Э2) — this test
is what keeps that true: running every migration from scratch must produce
the exact same `sqlite_master` as applying `schema.sql` directly.
"""

import re
from pathlib import Path

import aiosqlite

from stalbot.infrastructure.cache.migrations import run_migrations

_SCHEMA_SQL_PATH = Path(__file__).resolve().parents[4] / "src" / "stalbot" / "infrastructure"
_SCHEMA_SQL_PATH = _SCHEMA_SQL_PATH / "cache" / "schema.sql"

#: `ALTER TABLE x_new RENAME TO x` (migrations 0008-0010 rebuild three tables
#: that way, since SQLite cannot `ALTER ... CHECK`) makes SQLite rewrite the
#: stored DDL with the new name *quoted* — `CREATE TABLE "coupons"` — while
#: `schema.sql`, hand-written, spells it bare. The two schemas are identical;
#: only the quoting of the object's own name differs, and on a renamed table
#: that quoting is not something either side gets to choose. Everything after
#: the name — every column, type, and CHECK — is still compared verbatim.
_QUOTED_OBJECT_NAME = re.compile(
    r'^(CREATE (?:UNIQUE )?(?:TABLE|INDEX|VIEW|TRIGGER) (?:IF NOT EXISTS )?)"([^"]+)"'
)


def _normalize(sql: str) -> str:
    return _QUOTED_OBJECT_NAME.sub(r"\1\2", sql)


async def _sqlite_master_snapshot(conn: aiosqlite.Connection) -> set[tuple[str, str, str]]:
    cursor = await conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return {(row["type"], row["name"], _normalize(row["sql"])) async for row in cursor}


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


def test_normalize_only_unquotes_the_objects_own_name() -> None:
    """A quoted *column* name, or a quote anywhere else, must survive untouched."""
    assert _normalize('CREATE TABLE "coupons" (id INTEGER)') == "CREATE TABLE coupons (id INTEGER)"
    quoted_column = 'CREATE TABLE coupons ("id" INTEGER)'
    assert _normalize(quoted_column) == quoted_column
    assert (
        _normalize("CREATE UNIQUE INDEX ux_a ON a(b) WHERE c = '\"x\"'")
        == "CREATE UNIQUE INDEX ux_a ON a(b) WHERE c = '\"x\"'"
    )
