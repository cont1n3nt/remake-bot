"""Low-level batched Sheets access (see PLAN.md §7.1).

`SheetsClient` is the only place in the project that talks to the Google
Sheets API. Every other module — repositories, sync, services — goes
through it, which is what makes the two hard rules in PLAN.md §7.3
enforceable in one spot: `valueInputOption` is always `RAW` (never
`USER_ENTERED`), and every write is checked against
`infrastructure.sheets.protection` before the request leaves the process.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Final

import gspread
from google.oauth2.service_account import Credentials

from stalbot.config.settings import Settings
from stalbot.domain.errors import SheetStructureError, SheetsWriteConflictError
from stalbot.infrastructure.sheets import protection
from stalbot.infrastructure.sheets.a1 import a1_range, column_letter_to_index, parse_a1_range
from stalbot.infrastructure.sheets.layouts import (
    DATABASE_BLOCKS,
    DATABASE_SHEET,
    EXPECTED_SHEET_TITLES,
    HEADER_ROW,
    DatabaseBlock,
)
from stalbot.infrastructure.sheets.ratelimit import (
    REQUEST_TIMEOUT_SECONDS,
    ReentrantAsyncLock,
    SheetsRateLimiter,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

_SCOPES: Final = ("https://www.googleapis.com/auth/spreadsheets",)
_VALUE_INPUT_OPTION: Final = "RAW"
_UNFORMATTED: Final = "UNFORMATTED_VALUE"
_FORMULA: Final = "FORMULA"

CellGrid = Sequence[Sequence[object]]


class SheetsClient:
    """Async batch-only wrapper around `gspread`.

    Every call is a `values_batch_get`/`values_batch_update` (or, for the
    formula copy-down fallback, a raw `batch_update`) — never a
    single-range call — so one bot action costs at most a couple of API
    requests (PLAN.md §7.1).
    """

    def __init__(
        self, settings: Settings, *, rate_limiter: SheetsRateLimiter | None = None
    ) -> None:
        """Build a client. Nothing touches the network until first use.

        Args:
            settings: Validated application configuration.
            rate_limiter: Shared limiter; a fresh one is created if omitted.
        """
        self._settings = settings
        self._rate_limiter = rate_limiter or SheetsRateLimiter()
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None
        #: Cumulative request counts since process start, surfaced by
        #: `/healthcheck` and the per-minute metrics log (PLAN.md §12, M11).
        self.read_request_count = 0
        self.write_request_count = 0

    async def batch_get(self, ranges: Sequence[str]) -> dict[str, CellGrid]:
        """Read multiple A1 ranges in a single API request.

        Args:
            ranges: Sheet-qualified A1 ranges.

        Returns:
            Mapping from each *requested* range string to its cell grid
            (missing trailing rows/columns are simply absent, not padded).
            Keyed positionally against `ranges`, not against the API
            response's own `range` field — the API normalizes an
            open-ended range like `"DataBase!A3:H"` to a concrete
            `"DataBase!A3:H1598"` in its reply, which would never match the
            caller's original key.
        """
        spreadsheet = await self._ensure_open()
        await self._rate_limiter.acquire_read()
        requested = list(ranges)

        async def _do() -> dict[str, CellGrid]:
            raw = await asyncio.to_thread(
                spreadsheet.values_batch_get,
                requested,
                params={"valueRenderOption": _UNFORMATTED},
            )
            value_ranges = raw.get("valueRanges", [])
            return {
                ref: value_ranges[index].get("values", [])
                for index, ref in enumerate(requested)
                if index < len(value_ranges)
            }

        result = await retry_with_backoff(_do)
        self.read_request_count += 1
        return result

    async def batch_update(self, data: Mapping[str, CellGrid]) -> None:
        """Write multiple A1 ranges in a single API request.

        Every range is checked against `protection.ensure_writable` before
        any network call is made — a formula column write fails locally and
        instantly, never reaching the API. `valueInputOption` is hardcoded
        to `RAW`; the project never sends `USER_ENTERED`.

        Args:
            data: Mapping from A1 range to the cell grid to write there.
        """
        for ref in data:
            protection.ensure_writable(ref)

        spreadsheet = await self._ensure_open()
        sheets = sorted({_sheet_of(ref) for ref in data})
        locks = [self._rate_limiter.write_lock(sheet) for sheet in sheets]
        async with _AcquireAll(locks):
            await self._rate_limiter.acquire_write()

            async def _do() -> None:
                body = {
                    "valueInputOption": _VALUE_INPUT_OPTION,
                    "data": [
                        {"range": ref, "values": list(values)} for ref, values in data.items()
                    ],
                }
                await asyncio.to_thread(spreadsheet.values_batch_update, body)

            await retry_with_backoff(_do)
        self.write_request_count += 1

    def locked(self, sheet: str) -> ReentrantAsyncLock:
        """Return the lock serializing every write to `sheet` (INFRA1-6).

        `batch_update` only ever holds this lock around its own network
        call, which is not enough to protect a caller's full
        read -> compute -> write sequence: two concurrent calls can each
        read the same stale state, compute the same "next" id/row, and the
        second write silently clobbers the first (the exact class of race
        APP-5 narrowed but did not close for every caller). Wrap the whole
        sequence in `async with client.locked(sheet):` instead — the lock is
        reentrant, so `batch_update`'s own internal acquire for the same
        sheet, from the same task, does not deadlock against it.

        Args:
            sheet: Worksheet title whose writes should be serialized.
        """
        return self._rate_limiter.write_lock(sheet)

    async def write_verified(self, data: Mapping[str, CellGrid]) -> None:
        """Write cells, then read them back to confirm the write took.

        The read-back is compared cell-by-cell against what was sent, with
        the (shorter) read-back grid right-padded with `""` before
        comparing: the Sheets API trims trailing empty cells/rows from a
        read reply rather than padding them back in, so a written row that
        legitimately ends in an empty cell (e.g. no referrer) would
        otherwise come back shorter than what was sent and look like a
        mismatch that never happened.

        On a genuine mismatch, the written ranges are cleared again
        (best-effort compensation) and `SheetsWriteConflictError` is raised
        — the caller must not treat a verification failure as a partial
        success (PLAN.md §7.4).

        Args:
            data: Mapping from A1 range to the cell grid to write there.

        Raises:
            SheetsWriteConflictError: If any range's content, once read
                back, does not match what was sent.
        """
        await self.batch_update(data)
        readback = await self.batch_get(list(data.keys()))
        mismatched = [
            ref for ref, values in data.items() if not _grid_matches(values, readback.get(ref, []))
        ]
        if not mismatched:
            return

        blanks = {ref: _blank_like(data[ref]) for ref in mismatched}
        await self.batch_update(blanks)
        raise SheetsWriteConflictError(
            f"read-back verification failed, cleared and aborted for: {mismatched}"
        )

    async def read_until(
        self,
        ranges: Sequence[str],
        *,
        is_ready: Callable[[Mapping[str, CellGrid]], bool],
        attempts: int = 3,
        delay_seconds: float = 0.7,
    ) -> dict[str, CellGrid]:
        """Poll `batch_get` until a caller-supplied predicate is satisfied.

        Used to wait for the sheet to recalculate formula columns after a
        write (PLAN.md §7.4 step 4) — the predicate typically checks that a
        just-written row's formula cells are no longer empty.

        Args:
            ranges: Ranges to read on every attempt.
            is_ready: Called with the read result; return `True` to stop
                polling early.
            attempts: Maximum number of reads before giving up.
            delay_seconds: Delay between attempts.

        Returns:
            The last read result, whether or not `is_ready` was ever
            satisfied — callers inspect the result themselves.
        """
        result: dict[str, CellGrid] = {}
        for attempt in range(attempts):
            result = await self.batch_get(ranges)
            if is_ready(result):
                return result
            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)
        return result

    async def read_formula_extent(self, ref: str) -> int:
        """Find how many leading rows of a formula column are populated.

        Args:
            ref: A single-column A1 range, e.g. `"DataBase!F3:F2000"`.

        Returns:
            Count of consecutive non-empty rows from the start of `ref`
            (0 if the first row is already empty).
        """
        spreadsheet = await self._ensure_open()
        await self._rate_limiter.acquire_read()

        async def _do() -> CellGrid:
            raw = await asyncio.to_thread(
                spreadsheet.values_batch_get,
                [ref],
                params={"valueRenderOption": _FORMULA},
            )
            ranges = raw.get("valueRanges", [])
            values: CellGrid = ranges[0].get("values", []) if ranges else []
            return values

        values = await retry_with_backoff(_do)
        count = 0
        for row in values:
            if not row or not str(row[0]).strip():
                break
            count += 1
        return count

    async def copy_formula_down(
        self, sheet: str, *, columns: tuple[str, str], source_row: int, target_row: int
    ) -> None:
        """Copy a formula from one row to another via `copyPaste`.

        Reserve fallback for when a transaction is written past the last
        row the customer pre-extended formulas to (PLAN.md §7.3): the bot
        never composes formula text itself, it asks the API to copy the
        cell as-is.

        Args:
            sheet: Worksheet title.
            columns: `(first, last)` column letters of the contiguous
                formula range to copy (e.g. `("F", "G")`).
            source_row: Row to copy from (1-based).
            target_row: Row to copy into (1-based).
        """
        spreadsheet = await self._ensure_open()
        worksheet = await asyncio.to_thread(spreadsheet.worksheet, sheet)
        col_start = column_letter_to_index(columns[0]) - 1
        col_end = column_letter_to_index(columns[1])
        body = {
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": worksheet.id,
                            "startRowIndex": source_row - 1,
                            "endRowIndex": source_row,
                            "startColumnIndex": col_start,
                            "endColumnIndex": col_end,
                        },
                        "destination": {
                            "sheetId": worksheet.id,
                            "startRowIndex": target_row - 1,
                            "endRowIndex": target_row,
                            "startColumnIndex": col_start,
                            "endColumnIndex": col_end,
                        },
                        "pasteType": "PASTE_FORMULA",
                    }
                }
            ]
        }

        async def _do() -> None:
            await asyncio.to_thread(spreadsheet.batch_update, body)

        await retry_with_backoff(_do)

    async def validate_layout(self) -> None:
        """Check the live sheet titles and headers against expectations.

        Args: none.

        Raises:
            SheetStructureError: If a sheet is missing or a header block
                does not match, listing every mismatch found so the
                operator can fix all of them in one pass.
        """
        spreadsheet = await self._ensure_open()
        titles = {ws.title for ws in await asyncio.to_thread(spreadsheet.worksheets)}
        problems = [
            f"missing sheet: {title!r}" for title in EXPECTED_SHEET_TITLES if title not in titles
        ]

        ranges = [_header_range(block) for block in DATABASE_BLOCKS]
        headers = await self.batch_get(ranges)
        for block, ref in zip(DATABASE_BLOCKS, ranges, strict=True):
            row = headers.get(ref, [[]])
            actual = tuple(row[0]) if row else ()
            if actual != block.expected_headers:
                problems.append(
                    f"block {block.name!r} ({ref}) header mismatch: expected "
                    f"{block.expected_headers!r}, found {actual!r}"
                )

        if problems:
            raise SheetStructureError("; ".join(problems))

    async def _ensure_open(self) -> gspread.Spreadsheet:
        if self._spreadsheet is None:
            self._spreadsheet = await asyncio.to_thread(self._open_sync)
        return self._spreadsheet

    def _open_sync(self) -> gspread.Spreadsheet:
        return self._gspread_client().open_by_key(self._settings.spreadsheet_id)

    def _gspread_client(self) -> gspread.Client:
        if self._client is None:
            creds = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(self._settings.google_credentials_path), scopes=list(_SCOPES)
            )
            self._client = gspread.authorize(creds)
            # Belt and suspenders with `retry_with_backoff`'s own
            # `asyncio.wait_for` (INFRA1-11): that alone only abandons a
            # hung call's background thread, which could still complete
            # later and silently overwrite a newer write. A transport-level
            # timeout makes the underlying socket operation itself actually
            # abort, so the thread doesn't outlive the request that gave up
            # on it.
            self._client.http_client.set_timeout(REQUEST_TIMEOUT_SECONDS)
        return self._client


def _sheet_of(ref: str) -> str:
    return parse_a1_range(ref).sheet


def _header_range(block: DatabaseBlock) -> str:
    return a1_range(DATABASE_SHEET, block.col_start, HEADER_ROW, block.col_end, HEADER_ROW)


def _blank_like(values: CellGrid) -> CellGrid:
    return [["" for _ in row] for row in values]


def _grid_matches(written: CellGrid, read: CellGrid) -> bool:
    """Compare a written grid against its read-back, right-padding `read`.

    The Sheets API trims trailing empty cells/rows from a read reply rather
    than padding them back to the requested shape, so a missing trailing
    row or cell in `read` is treated as an explicit `""` rather than a
    mismatch.
    """
    for row_index, written_row in enumerate(written):
        read_row = read[row_index] if row_index < len(read) else []
        for col_index, written_cell in enumerate(written_row):
            read_cell = read_row[col_index] if col_index < len(read_row) else ""
            if written_cell != read_cell:
                return False
    return True


class _AcquireAll:
    """Acquire several locks (sorted order) as one context manager.

    If a later lock's `acquire()` raises or is cancelled partway through,
    every lock already acquired is released before the exception propagates
    (INFRA1-7) — otherwise those locks would stay held forever, since
    `__aexit__` never runs for a context manager whose `__aenter__` didn't
    complete. Not observable with today's single-sheet writes (`locks` is
    always length 1), but `_AcquireAll` is written to be correct for the
    multi-lock case its signature already promises.
    """

    def __init__(self, locks: Sequence[ReentrantAsyncLock]) -> None:
        self._locks = locks

    async def __aenter__(self) -> None:
        acquired: list[ReentrantAsyncLock] = []
        try:
            for lock in self._locks:
                await lock.acquire()
                acquired.append(lock)
        except BaseException:
            for lock in reversed(acquired):
                lock.release()
            raise

    async def __aexit__(self, *_exc_info: object) -> None:
        for lock in reversed(self._locks):
            lock.release()
