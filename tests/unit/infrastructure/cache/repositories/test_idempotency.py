"""Tests for `IdempotencyRepository` against a real (temp-file) SQLite connection."""

import aiosqlite

from stalbot.infrastructure.cache.repositories.idempotency import IdempotencyRepository


async def test_get_returns_none_when_unseen(connection: aiosqlite.Connection) -> None:
    repo = IdempotencyRepository(connection)
    assert await repo.get("interaction-1") is None


async def test_record_then_get_round_trips(connection: aiosqlite.Connection) -> None:
    repo = IdempotencyRepository(connection)
    await repo.record("interaction-1", 42, created_at="2026-08-02T12:00:00+03:00")

    assert await repo.get("interaction-1") == 42


async def test_record_is_idempotent_for_the_same_key(connection: aiosqlite.Connection) -> None:
    repo = IdempotencyRepository(connection)
    await repo.record("interaction-1", 42, created_at="2026-08-02T12:00:00+03:00")
    await repo.record("interaction-1", 999, created_at="2026-08-02T12:05:00+03:00")

    assert await repo.get("interaction-1") == 42  # first write wins, not overwritten


async def test_different_keys_are_independent(connection: aiosqlite.Connection) -> None:
    repo = IdempotencyRepository(connection)
    await repo.record("interaction-1", 42, created_at="2026-08-02T12:00:00+03:00")
    await repo.record("interaction-2", 43, created_at="2026-08-02T12:00:00+03:00")

    assert await repo.get("interaction-1") == 42
    assert await repo.get("interaction-2") == 43
