"""Tests for `stalbot.infrastructure.sheets.ratelimit`."""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
import requests
from gspread.exceptions import APIError

from stalbot.domain.errors import SheetsUnavailableError
from stalbot.infrastructure.sheets.ratelimit import (
    ReentrantAsyncLock,
    SheetsRateLimiter,
    TokenBucket,
    retry_with_backoff,
)


class _FakeResponse:
    """Stands in for `requests.Response` — just enough for `APIError.__init__`."""

    def __init__(self, code: int, message: str = "boom") -> None:
        self.text = message
        self._code = code
        self._message = message

    def json(self) -> dict[str, Any]:
        return {"error": {"code": self._code, "message": self._message, "status": "ERR"}}


def _api_error(code: int) -> APIError:
    return APIError(_FakeResponse(code))  # type: ignore[arg-type]


async def test_token_bucket_allows_immediate_acquire_within_capacity() -> None:
    bucket = TokenBucket(capacity=5, per_seconds=60)
    for _ in range(5):
        await bucket.acquire()  # must not block


async def test_token_bucket_blocks_once_exhausted() -> None:
    bucket = TokenBucket(capacity=1, per_seconds=0.2)
    await bucket.acquire()

    started = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - started

    assert elapsed >= 0.1


def test_write_lock_returns_same_lock_for_same_key() -> None:
    limiter = SheetsRateLimiter()
    assert limiter.write_lock("DataBase") is limiter.write_lock("DataBase")


def test_write_lock_returns_different_locks_for_different_keys() -> None:
    limiter = SheetsRateLimiter()
    assert limiter.write_lock("DataBase") is not limiter.write_lock("Мейн скуп")


async def test_retry_with_backoff_returns_on_first_success() -> None:
    operation = AsyncMock(return_value="ok")
    result = await retry_with_backoff(operation)
    assert result == "ok"
    operation.assert_awaited_once()


async def test_retry_with_backoff_retries_retryable_errors_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    operation = AsyncMock(side_effect=[_api_error(429), _api_error(503), "ok"])

    result = await retry_with_backoff(operation, max_attempts=5)

    assert result == "ok"
    assert operation.await_count == 3


async def test_retry_with_backoff_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    operation = AsyncMock(side_effect=_api_error(500))

    with pytest.raises(SheetsUnavailableError):
        await retry_with_backoff(operation, max_attempts=3)

    assert operation.await_count == 3


async def test_retry_with_backoff_does_not_retry_non_retryable_status() -> None:
    operation = AsyncMock(side_effect=_api_error(403))

    with pytest.raises(SheetsUnavailableError, match="non-retryable"):
        await retry_with_backoff(operation, max_attempts=5)

    operation.assert_awaited_once()


async def test_retry_with_backoff_retries_network_failures_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFRA1-3: a connection failure below `gspread`'s own `APIError` (DNS,
    reset, TLS — no HTTP response was ever received) must be retried too,
    not propagate untranslated."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    operation = AsyncMock(side_effect=[requests.exceptions.ConnectionError("boom"), "ok"])

    result = await retry_with_backoff(operation, max_attempts=3)

    assert result == "ok"
    assert operation.await_count == 2


async def test_retry_with_backoff_gives_up_after_max_attempts_on_network_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    operation = AsyncMock(side_effect=requests.exceptions.Timeout("boom"))

    with pytest.raises(SheetsUnavailableError):
        await retry_with_backoff(operation, max_attempts=2)

    assert operation.await_count == 2


async def test_retry_with_backoff_does_not_retry_a_non_transient_transport_error() -> None:
    """A bad URL/redirect loop is a config or programming bug, not a network
    hiccup — retrying it just delays an unavoidable failure."""
    operation = AsyncMock(side_effect=requests.exceptions.MissingSchema("boom"))

    with pytest.raises(SheetsUnavailableError, match="non-retryable"):
        await retry_with_backoff(operation, max_attempts=5)

    operation.assert_awaited_once()


async def test_retry_with_backoff_retries_a_hung_call_that_exceeds_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFRA1-11: `asyncio.to_thread` can't forcibly kill a stalled `gspread`
    call, so nothing would ever raise on its own — `retry_with_backoff` must
    itself give up on a single attempt that runs past `timeout_seconds`."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.Event().wait()  # hangs forever, like a stalled socket
        return "ok"

    result = await retry_with_backoff(operation, max_attempts=3, timeout_seconds=0.01)

    assert result == "ok"
    assert calls == 2


async def test_reentrant_lock_same_task_reentry_does_not_deadlock() -> None:
    lock = ReentrantAsyncLock()
    async with lock:
        async with lock:
            pass  # must return, not hang


async def test_reentrant_lock_fully_released_after_matching_release_calls() -> None:
    lock = ReentrantAsyncLock()
    async with lock:
        async with lock:
            pass
    acquired_by_other = False

    async def other() -> None:
        nonlocal acquired_by_other
        async with lock:
            acquired_by_other = True

    await asyncio.wait_for(other(), timeout=1)
    assert acquired_by_other


async def test_reentrant_lock_a_different_task_blocks_until_release() -> None:
    lock = ReentrantAsyncLock()
    order: list[str] = []

    async def holder() -> None:
        async with lock:
            order.append("holder-in")
            await asyncio.sleep(0.05)
            order.append("holder-out")

    async def waiter() -> None:
        await asyncio.sleep(0.01)  # let the holder acquire first
        async with lock:
            order.append("waiter-in")

    await asyncio.gather(holder(), waiter())
    assert order == ["holder-in", "holder-out", "waiter-in"]
