"""Sheets API throttling: token buckets, write locks, retry with backoff.

Google Sheets allows 60 read and 60 write requests per minute per project.
`SheetsRateLimiter` keeps the bot under that ceiling with an 80% safety
margin, serializes writes to the same logical target so concurrent `/add`
calls cannot race each other, and retries transient failures before giving
up (see PLAN.md §7.2).
"""

import asyncio
import logging
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Final

from gspread.exceptions import APIError

from stalbot.domain.errors import SheetsUnavailableError

logger = logging.getLogger(__name__)

#: Google's own ceiling is 60/min; keep a 20% safety margin under it.
_SAFETY_MARGIN: Final = 0.8
REQUESTS_PER_MINUTE: Final = 60
BUCKET_CAPACITY: Final = int(REQUESTS_PER_MINUTE * _SAFETY_MARGIN)

_RETRYABLE_STATUS_CODES: Final = frozenset({429, 500, 503})
MAX_RETRY_ATTEMPTS: Final = 5
_BASE_BACKOFF_SECONDS: Final = 0.5
_MAX_BACKOFF_SECONDS: Final = 8.0


class TokenBucket:
    """A classic token bucket: `capacity` tokens, refilled continuously."""

    def __init__(self, capacity: int, *, per_seconds: float = 60.0) -> None:
        """Build a bucket that refills to `capacity` every `per_seconds`.

        Args:
            capacity: Maximum (and initial) number of tokens.
            per_seconds: Time window the capacity refills over.
        """
        self._capacity = capacity
        self._refill_rate = capacity / per_seconds
        self._tokens = float(capacity)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                deficit = 1 - self._tokens
                wait_seconds = deficit / self._refill_rate
            await asyncio.sleep(wait_seconds)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._updated_at = now


class SheetsRateLimiter:
    """Read/write token buckets plus per-key write locks for `SheetsClient`."""

    def __init__(self, *, bucket_capacity: int = BUCKET_CAPACITY) -> None:
        """Build independent read and write buckets.

        Args:
            bucket_capacity: Tokens per minute for each bucket.
        """
        self._read_bucket = TokenBucket(bucket_capacity)
        self._write_bucket = TokenBucket(bucket_capacity)
        self._write_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire_read(self) -> None:
        """Wait for a free read slot."""
        await self._read_bucket.acquire()

    async def acquire_write(self) -> None:
        """Wait for a free write slot."""
        await self._write_bucket.acquire()

    def write_lock(self, key: str) -> asyncio.Lock:
        """Return the lock serializing writes to a given logical target.

        Args:
            key: Anything that identifies the contended region, e.g. a
                sheet name or an A1 range.
        """
        return self._write_locks[key]


async def retry_with_backoff[T](
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = MAX_RETRY_ATTEMPTS,
) -> T:
    """Run `operation`, retrying transient Sheets API failures.

    Retries on HTTP `429`/`500`/`503` with exponential backoff and jitter.
    Any other `APIError`, or exhausting `max_attempts`, propagates as
    `SheetsUnavailableError`.

    Args:
        operation: A zero-argument async callable to run.
        max_attempts: Maximum number of attempts before giving up.

    Returns:
        Whatever `operation` returns.

    Raises:
        SheetsUnavailableError: If every attempt failed, or a non-retryable
            API error was raised.
    """
    last_error: APIError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except APIError as exc:
            if exc.code not in _RETRYABLE_STATUS_CODES:
                raise SheetsUnavailableError(f"non-retryable Sheets API error: {exc}") from exc
            last_error = exc
            if attempt == max_attempts:
                break
            delay = min(_MAX_BACKOFF_SECONDS, _BASE_BACKOFF_SECONDS * 2 ** (attempt - 1))
            jitter = random.uniform(0, delay / 2)  # noqa: S311 - retry jitter, not security-sensitive
            logger.warning(
                "sheets API error (attempt %d/%d, code=%s), retrying in %.1fs",
                attempt,
                max_attempts,
                exc.code,
                delay + jitter,
            )
            await asyncio.sleep(delay + jitter)
    raise SheetsUnavailableError(
        f"Sheets API stayed unreachable after {max_attempts} attempts: {last_error}"
    ) from last_error
