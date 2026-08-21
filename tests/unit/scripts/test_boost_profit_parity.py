"""Characterization test: boost profit parity (sqlite_migration.md §II.3, §VI.2, Э5).

`Выгода = (Цена продажи − Себестоимость) × Количество` must match `БУСТЫ`'s
own bottom table (`boosts_bottom.csv`, r16:r21) — the last unchecked item on
Э5's readiness line ("выгода по «БУСТЫ» сходится до рубля").

`Себестоимость` here is the sheet row's own `cost` column, same as the
plan's worked examples (§II.3: "Гороховый суп: (6 500 − 4 613) × 4" — 4613 is
that row's own displayed cost, not independently recomputed). Deliberately
*not* `shelter_cost` from `ShelterRepository.get_cost()`: that table is
recomputed from each ingredient's own current price and can legitimately
drift from a recipe row's cached `unit_price` reference for a crafted
ingredient — exactly the staleness `test_import_shelter.py`'s
`test_level_e_leaf_item_costs_match_sheet_totals` already documents and
scopes around. This test is only about the profit *formula*, not about
whether `compute_costs()` agrees with the sheet's cache.

Only `price_sell` comes from a real integration point (`CatalogItemsRepository`,
via the Э4 importer) — everything else is the CSV's own numbers, so a
regression in the formula itself (not in cost recomputation) is what this
guards against.
"""

import csv
import importlib.util
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest_asyncio

from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository

_SHEETS_MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "import_from_sheets.py"
_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "sheet_snapshot_2026-08-10"
_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Distinct module name from test_import_shelter.py's own `_load_module` call
# for the same file — `sys.modules` is process-global and re-registering the
# same name from a different test module would clobber the other's copy.
sheets_mod = _load_module("import_from_sheets_boost_profit_parity", _SHEETS_MODULE_PATH)


@pytest_asyncio.fixture
async def catalog_db(tmp_path: Path) -> AsyncIterator[CacheDb]:
    """Closes on teardown via `finally` — see `test_import_shelter.py`'s
    `shelter_only_db` docstring for why (an `assert` failure must not leak
    `aiosqlite`'s non-daemon connection thread and hang `pytest` afterward).
    """
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    try:
        await sheets_mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
        yield cache_db
    finally:
        await cache_db.close()


def _load_boost_rows() -> list[dict[str, str]]:
    with (_SNAPSHOT_DIR / "boosts_bottom.csv").open(encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row["name"].strip()]


async def test_boost_profit_matches_sheet_bottom_table(catalog_db: CacheDb) -> None:
    connection = await catalog_db.connect()
    catalog_repo = CatalogItemsRepository(connection)

    catalog_by_name = {
        item.name: item for item in await catalog_repo.all() if item.category == "boost"
    }

    checked = 0
    for row in _load_boost_rows():
        qty_text = row["qty"].strip()
        if not qty_text:
            continue  # blank qty -> sheet's own profit is a trivial 0, no formula to check
        name = row["name"].strip()
        catalog_item = catalog_by_name.get(name)
        assert catalog_item is not None, name
        assert catalog_item.price_sell is not None, name

        qty = int(qty_text)
        cost = Decimal(row["cost"])
        computed_profit = (Decimal(catalog_item.price_sell) - cost) * qty
        expected_profit = Decimal(row["profit"])
        assert computed_profit == expected_profit, name
        checked += 1

    assert checked >= 1  # guards against a vacuous pass if the fixture ever loses its one qty row
