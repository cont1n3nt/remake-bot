"""Tests for `stalbot.infrastructure.sheets.client.SheetsClient`.

All network access is stubbed out via a `MagicMock(spec=gspread.Spreadsheet)`
injected in place of `_ensure_open` — nothing here touches the real API.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import gspread
import pytest
from google.oauth2.service_account import Credentials

from stalbot.domain.errors import (
    ProtectedRangeWriteError,
    SheetStructureError,
    SheetsWriteConflictError,
)
from stalbot.infrastructure.sheets.client import CellGrid, SheetsClient, _AcquireAll
from stalbot.infrastructure.sheets.layouts import DATABASE_BLOCKS, EXPECTED_SHEET_TITLES
from stalbot.infrastructure.sheets.ratelimit import REQUEST_TIMEOUT_SECONDS, ReentrantAsyncLock


def _client_with_fake_spreadsheet(spreadsheet: MagicMock) -> SheetsClient:
    settings = MagicMock()
    settings.google_credentials_path = "unused.json"
    settings.spreadsheet_id = "unused"
    client = SheetsClient(settings)
    client._ensure_open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    return client


def _fake_spreadsheet() -> MagicMock:
    return MagicMock(spec=gspread.Spreadsheet)


def test_gspread_client_sets_a_transport_level_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """INFRA1-11: `asyncio.wait_for`'s timeout alone only abandons a hung
    call's background thread (it can't be killed) — the transport-level
    timeout is what actually aborts the underlying socket operation, so an
    orphaned write can't complete later and silently clobber a newer one."""
    monkeypatch.setattr(
        Credentials,
        "from_service_account_file",
        MagicMock(return_value=MagicMock()),
    )
    fake_gspread_client = MagicMock()
    monkeypatch.setattr(gspread, "authorize", MagicMock(return_value=fake_gspread_client))
    settings = MagicMock()
    settings.google_credentials_path = "unused.json"
    client = SheetsClient(settings)

    client._gspread_client()

    fake_gspread_client.http_client.set_timeout.assert_called_once_with(REQUEST_TIMEOUT_SECONDS)


def test_gspread_client_reuses_the_cached_client_without_resetting_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Credentials,
        "from_service_account_file",
        MagicMock(return_value=MagicMock()),
    )
    fake_gspread_client = MagicMock()
    monkeypatch.setattr(gspread, "authorize", MagicMock(return_value=fake_gspread_client))
    settings = MagicMock()
    settings.google_credentials_path = "unused.json"
    client = SheetsClient(settings)

    first = client._gspread_client()
    second = client._gspread_client()

    assert first is second
    fake_gspread_client.http_client.set_timeout.assert_called_once_with(REQUEST_TIMEOUT_SECONDS)


def _value_ranges(payload: dict[str, list[list[Any]]]) -> dict[str, Any]:
    return {"valueRanges": [{"range": ref, "values": values} for ref, values in payload.items()]}


async def test_batch_get_maps_ranges_to_values() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges(
        {"DataBase!A3:H": [["31.07.2026", "nick"]]}
    )
    client = _client_with_fake_spreadsheet(spreadsheet)

    result = await client.batch_get(["DataBase!A3:H"])

    assert result == {"DataBase!A3:H": [["31.07.2026", "nick"]]}


async def test_batch_get_omits_ranges_with_no_data() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = {"valueRanges": []}
    client = _client_with_fake_spreadsheet(spreadsheet)

    result = await client.batch_get(["DataBase!A3:H"])

    assert result == {}


async def test_batch_update_rejects_protected_range_before_any_network_call() -> None:
    spreadsheet = _fake_spreadsheet()
    client = _client_with_fake_spreadsheet(spreadsheet)

    with pytest.raises(ProtectedRangeWriteError):
        await client.batch_update({"DataBase!F3": [[1]]})

    spreadsheet.values_batch_update.assert_not_called()


async def test_batch_update_sends_raw_value_input_option() -> None:
    spreadsheet = _fake_spreadsheet()
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.batch_update({"DataBase!A3:E3": [["31.07.2026", "nick", True, False, 100]]})

    spreadsheet.values_batch_update.assert_called_once()
    (body,), _ = spreadsheet.values_batch_update.call_args
    assert body["valueInputOption"] == "RAW"
    assert body["data"] == [
        {"range": "DataBase!A3:E3", "values": [["31.07.2026", "nick", True, False, 100]]}
    ]


async def test_batch_get_increments_the_read_request_counter() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges({})
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.batch_get(["DataBase!A3:H"])
    await client.batch_get(["DataBase!A3:H"])

    assert client.read_request_count == 2
    assert client.write_request_count == 0


async def test_batch_update_increments_the_write_request_counter() -> None:
    spreadsheet = _fake_spreadsheet()
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.batch_update({"DataBase!A3:E3": [["31.07.2026", "nick", True, False, 100]]})

    assert client.write_request_count == 1
    assert client.read_request_count == 0


async def test_rejected_write_does_not_increment_the_write_request_counter() -> None:
    spreadsheet = _fake_spreadsheet()
    client = _client_with_fake_spreadsheet(spreadsheet)

    with pytest.raises(ProtectedRangeWriteError):
        await client.batch_update({"DataBase!F3": [[1]]})

    assert client.write_request_count == 0


async def test_write_verified_succeeds_when_readback_matches() -> None:
    spreadsheet = _fake_spreadsheet()
    written = {"DataBase!I3": [[123]]}
    spreadsheet.values_batch_get.return_value = _value_ranges(written)
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.write_verified(written)  # must not raise

    assert spreadsheet.values_batch_update.call_count == 1  # only the real write, no compensation


async def test_write_verified_tolerates_trailing_empty_cell_trimmed_from_readback() -> None:
    """The Sheets API trims a trailing empty cell from its reply instead of padding it back."""
    spreadsheet = _fake_spreadsheet()
    written = {"DataBase!A3:E3": [["31.07.2026", "nick", 100, "purchase", ""]]}
    spreadsheet.values_batch_get.return_value = _value_ranges(
        {"DataBase!A3:E3": [["31.07.2026", "nick", 100, "purchase"]]}
    )
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.write_verified(written)  # must not raise

    assert spreadsheet.values_batch_update.call_count == 1  # only the real write, no compensation


async def test_write_verified_tolerates_trailing_empty_row_trimmed_from_readback() -> None:
    spreadsheet = _fake_spreadsheet()
    written: dict[str, CellGrid] = {"DataBase!I3:I4": [[123], [""]]}
    spreadsheet.values_batch_get.return_value = _value_ranges({"DataBase!I3:I4": [[123]]})
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.write_verified(written)  # must not raise

    assert spreadsheet.values_batch_update.call_count == 1


async def test_write_verified_clears_and_raises_on_mismatch() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges({"DataBase!I3": [[999]]})
    client = _client_with_fake_spreadsheet(spreadsheet)

    with pytest.raises(SheetsWriteConflictError):
        await client.write_verified({"DataBase!I3": [[123]]})

    assert spreadsheet.values_batch_update.call_count == 2
    compensation_body = spreadsheet.values_batch_update.call_args.args[0]
    assert compensation_body["data"] == [{"range": "DataBase!I3", "values": [[""]]}]


async def test_write_verified_rejects_a_genuinely_shorter_readback_row() -> None:
    """A missing non-empty trailing cell is a real mismatch, not trimming."""
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges({"DataBase!A3:B3": [["nick"]]})
    client = _client_with_fake_spreadsheet(spreadsheet)

    with pytest.raises(SheetsWriteConflictError):
        await client.write_verified({"DataBase!A3:B3": [["nick", 100]]})


async def test_read_until_stops_at_first_ready_result() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges({"DataBase!F3": [[5]]})
    client = _client_with_fake_spreadsheet(spreadsheet)

    result = await client.read_until(["DataBase!F3"], is_ready=lambda r: True, attempts=5)

    assert result == {"DataBase!F3": [[5]]}
    assert spreadsheet.values_batch_get.call_count == 1


async def test_read_until_retries_until_attempts_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges({"DataBase!F3": [[""]]})
    client = _client_with_fake_spreadsheet(spreadsheet)

    result = await client.read_until(["DataBase!F3"], is_ready=lambda r: False, attempts=3)

    assert result == {"DataBase!F3": [[""]]}
    assert spreadsheet.values_batch_get.call_count == 3


async def test_read_formula_extent_counts_consecutive_nonempty_rows() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges(
        {"DataBase!F3:F": [["=A"], ["=B"], ["=C"], [""], ["=D"]]}
    )
    client = _client_with_fake_spreadsheet(spreadsheet)

    extent = await client.read_formula_extent("DataBase!F3:F")

    assert extent == 3


async def test_read_formula_extent_zero_when_first_row_empty() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges({"DataBase!F3:F": []})
    client = _client_with_fake_spreadsheet(spreadsheet)

    assert await client.read_formula_extent("DataBase!F3:F") == 0


async def test_copy_formula_down_builds_expected_copy_paste_request() -> None:
    spreadsheet = _fake_spreadsheet()
    worksheet = MagicMock()
    worksheet.id = 999
    spreadsheet.worksheet.return_value = worksheet
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.copy_formula_down("DataBase", columns=("F", "G"), source_row=850, target_row=851)

    (body,), _ = spreadsheet.batch_update.call_args
    copy_paste = body["requests"][0]["copyPaste"]
    assert copy_paste["source"] == {
        "sheetId": 999,
        "startRowIndex": 849,
        "endRowIndex": 850,
        "startColumnIndex": 5,
        "endColumnIndex": 7,
    }
    assert copy_paste["destination"]["startRowIndex"] == 850
    assert copy_paste["destination"]["endRowIndex"] == 851
    assert copy_paste["pasteType"] == "PASTE_FORMULA"


async def test_validate_layout_passes_when_titles_and_headers_match() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.worksheets.return_value = [
        MagicMock(title=title) for title in EXPECTED_SHEET_TITLES
    ]
    header_payload = {
        f"DataBase!{block.col_start}2:{block.col_end}2": [list(block.expected_headers)]
        for block in DATABASE_BLOCKS
    }
    spreadsheet.values_batch_get.return_value = _value_ranges(header_payload)
    client = _client_with_fake_spreadsheet(spreadsheet)

    await client.validate_layout()  # must not raise


async def test_validate_layout_raises_when_a_sheet_is_missing() -> None:
    spreadsheet = _fake_spreadsheet()
    remaining = [t for t in EXPECTED_SHEET_TITLES if t != "БУСТЫ"]
    spreadsheet.worksheets.return_value = [MagicMock(title=title) for title in remaining]
    spreadsheet.values_batch_get.return_value = _value_ranges({})
    client = _client_with_fake_spreadsheet(spreadsheet)

    with pytest.raises(SheetStructureError, match="БУСТЫ"):
        await client.validate_layout()


async def test_locked_is_reentrant_with_batch_update_s_own_internal_lock() -> None:
    """INFRA1-6: a caller wrapping a read -> compute -> write sequence in
    `async with client.locked(sheet):` must not deadlock when that sequence
    calls `batch_update`, which acquires the very same per-sheet lock again
    internally for its own network write."""
    spreadsheet = _fake_spreadsheet()
    client = _client_with_fake_spreadsheet(spreadsheet)

    async def _sequence() -> None:
        async with client.locked("DataBase"):
            row = ["31.07.2026", "nick", True, False, 100]
            await client.batch_update({"DataBase!A3:E3": [row]})

    await asyncio.wait_for(_sequence(), timeout=1)

    spreadsheet.values_batch_update.assert_called_once()


async def test_locked_returns_the_same_lock_batch_update_acquires_for_that_sheet() -> None:
    client = _client_with_fake_spreadsheet(_fake_spreadsheet())
    assert client.locked("DataBase") is client.locked("DataBase")


async def test_acquire_all_releases_already_acquired_locks_if_a_later_acquire_fails() -> None:
    """INFRA1-7: if a later lock's `acquire()` raises partway through
    `_AcquireAll.__aenter__`, every lock already acquired must be released —
    `__aexit__` never runs for a context manager whose `__aenter__` didn't
    complete, so without this the first lock would stay held forever."""
    first = ReentrantAsyncLock()

    class _BoomLock:
        async def acquire(self) -> None:
            raise RuntimeError("boom")

        def release(self) -> None:
            raise AssertionError("must not be released — it was never acquired")

    with pytest.raises(RuntimeError, match="boom"):
        async with _AcquireAll([first, _BoomLock()]):  # type: ignore[list-item]
            pass

    acquired_by_other = False

    async def other() -> None:
        nonlocal acquired_by_other
        async with first:
            acquired_by_other = True

    await asyncio.wait_for(other(), timeout=1)
    assert acquired_by_other


async def test_validate_layout_raises_when_headers_mismatch() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.worksheets.return_value = [
        MagicMock(title=title) for title in EXPECTED_SHEET_TITLES
    ]
    header_payload = {
        f"DataBase!{block.col_start}2:{block.col_end}2": [["wrong", "headers"]]
        for block in DATABASE_BLOCKS
    }
    spreadsheet.values_batch_get.return_value = _value_ranges(header_payload)
    client = _client_with_fake_spreadsheet(spreadsheet)

    with pytest.raises(SheetStructureError, match="header mismatch"):
        await client.validate_layout()
