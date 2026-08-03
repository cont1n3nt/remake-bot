"""Tests for `stalbot.infrastructure.sheets.client.SheetsClient`.

All network access is stubbed out via a `MagicMock(spec=gspread.Spreadsheet)`
injected in place of `_ensure_open` — nothing here touches the real API.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import gspread
import pytest

from stalbot.domain.errors import (
    ProtectedRangeWriteError,
    SheetStructureError,
    SheetsWriteConflictError,
)
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.infrastructure.sheets.layouts import DATABASE_BLOCKS, EXPECTED_SHEET_TITLES


def _client_with_fake_spreadsheet(spreadsheet: MagicMock) -> SheetsClient:
    settings = MagicMock()
    settings.google_credentials_path = "unused.json"
    settings.spreadsheet_id = "unused"
    client = SheetsClient(settings)
    client._ensure_open = AsyncMock(return_value=spreadsheet)  # type: ignore[method-assign]
    return client


def _fake_spreadsheet() -> MagicMock:
    return MagicMock(spec=gspread.Spreadsheet)


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


async def test_write_verified_clears_and_raises_on_mismatch() -> None:
    spreadsheet = _fake_spreadsheet()
    spreadsheet.values_batch_get.return_value = _value_ranges({"DataBase!I3": [[999]]})
    client = _client_with_fake_spreadsheet(spreadsheet)

    with pytest.raises(SheetsWriteConflictError):
        await client.write_verified({"DataBase!I3": [[123]]})

    assert spreadsheet.values_batch_update.call_count == 2
    compensation_body = spreadsheet.values_batch_update.call_args.args[0]
    assert compensation_body["data"] == [{"range": "DataBase!I3", "values": [[""]]}]


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
