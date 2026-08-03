"""Shared fixtures for cache-layer tests: a real, migrated, on-disk SQLite connection."""

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.infrastructure.cache.db import CacheDb


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """A fresh, schema-migrated SQLite connection backed by a temp file."""
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()
    yield conn
    await db.close()


@pytest.fixture
def synced_at() -> str:
    """A fixed ISO timestamp for repository upserts that need one."""
    return "2026-08-02T12:00:00+03:00"
