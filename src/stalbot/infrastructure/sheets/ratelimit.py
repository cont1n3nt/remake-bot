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

import requests
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

#: Transient transport failures worth retrying (INFRA1-3): the connection
#: never delivered a response at all, so there is a real chance a retry
#: succeeds. Deliberately narrower than `requests.exceptions.RequestException`
#: — that also covers config/programming errors (`InvalidURL`,
#: `MissingSchema`, `TooManyRedirects`), which would just waste a ~15s
#: backoff cycle before failing anyway, and should surface immediately.
_RETRYABLE_TRANSPORT_ERRORS: Final = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

#: Ceiling on a single underlying Sheets call (INFRA1-11). `gspread` runs
#: synchronously inside `asyncio.to_thread`, so a hung TCP connection would
#: otherwise never raise anything on its own — one stalled call would block
#: whichever lock it was made under (e.g. `TransactionService`'s process-wide
#: lock, or `SheetsClient.locked()`) forever, not just the request that
#: triggered it. `asyncio.wait_for` alone only abandons the blocked thread
#: (Python cannot forcibly kill it) — that frees the lock, but the orphaned
#: thread could still complete *later* and silently overwrite a newer write
#: that landed after the timeout gave up on it. `SheetsClient` also feeds
#: this same value to `gspread`'s own `HTTPClient.set_timeout()`, so the
#: underlying socket operation itself is what actually aborts, not just our
#: `asyncio` wrapper around it — belt and suspenders, not either/or.
#: Public (not `_`-prefixed): shared with `client.py` for exactly that reason.
REQUEST_TIMEOUT_SECONDS: Final = 20.0


class TokenBucket:
    """A classic token bucket: `capacity` tokens, refilled continuously."""

    def __init__(
        self,
        capacity: int,
        *,
        per_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Build a bucket that refills to `capacity` every `per_seconds`.

        Args:
            capacity: Maximum (and initial) number of tokens.
            per_seconds: Time window the capacity refills over.
            clock: Monotonic time source (TEST-8). Defaults to
                `time.monotonic`; overridable so tests can assert exact
                refill math against a controlled clock instead of the real
                one, without sleeping for real seconds.
        """
        self._capacity = capacity
        self._refill_rate = capacity / per_seconds
        self._tokens = float(capacity)
        self._clock = clock or time.monotonic
        self._updated_at = self._clock()
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
        now = self._clock()
        elapsed = now - self._updated_at
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._updated_at = now


class ReentrantAsyncLock:
    """An `asyncio.Lock` the same task may re-acquire without deadlocking.

    `SheetsClient.locked(sheet)` (INFRA1-6) lets a caller serialize a whole
    read -> compute -> write sequence, but `batch_update` also acquires the
    very same per-sheet lock internally for its own network write. A plain
    `asyncio.Lock` would deadlock a task against itself on that inner
    acquire; this tracks the owning task and depth so a nested acquire from
    the same task is a no-op, while a different task still blocks normally.

    Caution: "the same task" means literally the same `asyncio.Task` object.
    `retry_with_backoff`'s `asyncio.wait_for(operation(), ...)` runs
    `operation` in a *new child task* (`wait_for` wraps a bare coroutine via
    `ensure_future`), not the caller's own task — so code that already holds
    this lock and then calls something that acquires it again from *inside*
    a `retry_with_backoff`-wrapped operation would not be recognized as a
    reentry and would deadlock (the parent task blocks awaiting the child,
    the child blocks awaiting a lock only the parent could release). Nothing
    in this codebase does that today — `batch_update`'s own reacquire
    happens directly in the caller's task, before `retry_with_backoff` is
    ever entered — but it is the one shape of reentry this class does not
    protect against.
    """

    def __init__(self) -> None:
        """Build an unlocked, unowned lock."""
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[object] | None = None
        self._depth = 0

    async def acquire(self) -> None:
        """Acquire the lock, or re-enter it if the current task already holds it."""
        current = asyncio.current_task()
        if current is not None and self._owner is current:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = current
        self._depth = 1

    def release(self) -> None:
        """Release one level of ownership, unlocking once depth reaches zero."""
        self._depth -= 1
        if self._depth <= 0:
            self._depth = 0
            self._owner = None
            self._lock.release()

    async def __aenter__(self) -> None:
        """Acquire the lock for `async with` usage."""
        await self.acquire()

    async def __aexit__(self, *_exc_info: object) -> None:
        """Release the lock for `async with` usage."""
        self.release()


class SheetsRateLimiter:
    """Read/write token buckets plus per-key write locks for `SheetsClient`."""

    def __init__(self, *, bucket_capacity: int = BUCKET_CAPACITY) -> None:
        """Build independent read and write buckets.

        Args:
            bucket_capacity: Tokens per minute for each bucket.
        """
        self._read_bucket = TokenBucket(bucket_capacity)
        self._write_bucket = TokenBucket(bucket_capacity)
        self._write_locks: defaultdict[str, ReentrantAsyncLock] = defaultdict(ReentrantAsyncLock)

    async def acquire_read(self) -> None:
        """Wait for a free read slot."""
        await self._read_bucket.acquire()

    async def acquire_write(self) -> None:
        """Wait for a free write slot."""
        await self._write_bucket.acquire()

    def write_lock(self, key: str) -> ReentrantAsyncLock:
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
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
) -> T:
    """Run `operation`, retrying transient Sheets API/network failures.

    Retries on HTTP `429`/`500`/`503`, on a transient `requests` transport
    failure (connection reset, DNS, TLS — these surface below `gspread`'s
    own `APIError`, e.g. no HTTP response was ever received), and on a
    single attempt exceeding `timeout_seconds` (INFRA1-11) — all with
    exponential backoff and jitter. Any other `APIError`, a non-transient
    transport error (bad URL, redirect loop — a config/programming bug, not
    a network hiccup), or exhausting `max_attempts`, propagates as
    `SheetsUnavailableError`.

    Args:
        operation: A zero-argument async callable to run.
        max_attempts: Maximum number of attempts before giving up.
        timeout_seconds: Ceiling on a single attempt before it counts as a
            retryable failure.

    Returns:
        Whatever `operation` returns.

    Raises:
        SheetsUnavailableError: If every attempt failed, or a non-retryable
            API error was raised.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        error_code: object
        try:
            return await asyncio.wait_for(operation(), timeout=timeout_seconds)
        except APIError as exc:
            if exc.code not in _RETRYABLE_STATUS_CODES:
                raise SheetsUnavailableError(f"non-retryable Sheets API error: {exc}") from exc
            last_error = exc
            error_code = exc.code
        except (requests.exceptions.RequestException, TimeoutError) as exc:
            if isinstance(exc, requests.exceptions.RequestException) and not isinstance(
                exc, _RETRYABLE_TRANSPORT_ERRORS
            ):
                raise SheetsUnavailableError(f"non-retryable transport error: {exc}") from exc
            last_error = exc
            error_code = type(exc).__name__

        if attempt == max_attempts:
            break
        delay = min(_MAX_BACKOFF_SECONDS, _BASE_BACKOFF_SECONDS * 2 ** (attempt - 1))
        jitter = random.uniform(0, delay / 2)  # noqa: S311 - retry jitter, not security-sensitive
        logger.warning(
            "sheets API error (attempt %d/%d, code %s), retrying in %.1fs",
            attempt,
            max_attempts,
            error_code,
            delay + jitter,
        )
        await asyncio.sleep(delay + jitter)
    raise SheetsUnavailableError(
        f"Sheets API stayed unreachable after {max_attempts} attempts: {last_error}"
    ) from last_error
