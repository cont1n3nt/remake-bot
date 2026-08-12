"""End-to-end test for `scripts/import_shelter.py` against the real Э0
snapshot (sqlite_migration.md §VII, Э5's characterization levels E-H).

Ground truth is `shelter_prices.csv`'s own "total" column (`Итог` — what
the live sheet's `H = G ?? F` formula already computed) and
`shelter_tech_costs.csv`'s "Итоговая цена" for the 29 multi-profession
items — not the plan's worked examples, which predate this snapshot.
"""

import csv
import importlib.util
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest_asyncio

from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository

_SHEETS_MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "import_from_sheets.py"
_SHELTER_MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "import_shelter.py"
_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "sheet_snapshot_2026-08-10"
_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sheets_mod = _load_module("import_from_sheets", _SHEETS_MODULE_PATH)
shelter_mod = _load_module("import_shelter", _SHELTER_MODULE_PATH)


@pytest_asyncio.fixture
async def shelter_only_db(tmp_path: Path) -> AsyncIterator[CacheDb]:
    """A DB with only the shelter side imported (no catalog_items to bridge).

    Closes on teardown via `finally`, not a trailing call in each test body —
    an `assert` failure partway through a test must not skip `close()` and
    leak `aiosqlite`'s non-daemon connection thread, which would otherwise
    hang the whole `pytest` process after the run reports its result.
    """
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    try:
        await shelter_mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
        yield cache_db
    finally:
        await cache_db.close()


@pytest_asyncio.fixture
async def full_db(tmp_path: Path) -> AsyncIterator[tuple[CacheDb, Any, Any]]:
    """Both importers run against one DB — what a real cutover would do.

    See `shelter_only_db` for why teardown closes rather than the test body.
    """
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    try:
        sheets_report = await sheets_mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
        shelter_report = await shelter_mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
        yield cache_db, sheets_report, shelter_report
    finally:
        await cache_db.close()


#: 21 names appear more than once in `shelter_prices.csv` with genuinely
#: *different* `total` values per occurrence (not just repeated identical
#: rows, like «Ковер»'s 5 identical rows) — e.g. «Медь» at both 129 and
#: 1892. Which occurrence the live sheet's own downstream formulas treat
#: as canonical isn't recoverable from the CSV alone; both this test and
#: `import_shelter.py`'s `price_by_name` consistently take the first
#: occurrence (Э0's row order), so import and ground truth agree with each
#: other, but neither is *provably* the sheet's own intent for these 21.
#: Flagged for the owner, not guessed past that (sqlite_migration.md Э5).
_AMBIGUOUS_DUPLICATE_PRICE_NAMES: Final = frozenset(
    {
        "«ТОПОТ»",
        "Алюминиевый порошок",
        "Аномальные гены",
        "Батарея холодного синтеза",
        "Биогаз",
        "Бутылка чистой воды",
        "Газовый баллон",
        "Гороховый суп",
        "Дымный порох",
        "Защитное снаряжение",
        "Кресло",
        "Медь",
        "Операционный усилитель",
        "Пластиковая бутылка",
        "Полимеры",
        "Продвинутый электрод",
        "Свинец",
        "Сталь",
        "Стул",
        "Термическая смесь",
        "Химический реактор",
        "Электросмесь",
    }
)

#: A confirmed **live-sheet bug**, not an Э0 export artifact — verified
#: directly against `убежка.xlsx`'s raw cells: block 8 ("Сырье и
#: материалы")'s "Итог" column is off by one row starting here. `AI3`
#: ("Моя цена" for «Светящийся сахар») = 3800, which should make `AJ3`
#: ("Итог") = 3800 too (§V.2 rule 1) — but `AJ3` = 3827 (Абразив's *own*
#: "Себестоимость", one row down) and `AJ4` (Абразив's own "Итог") = 3800
#: («Светящийся сахар»'s "Моя цена", one row up). `compute_costs()`
#: correctly uses each item's own `my_kopeks`/`market_kopeks` (3800/3827
#: for «Светящийся сахар», 170/176 for Абразив) — the sheet's own cached
#: "Итог" is simply wrong for these two rows, so there is nothing to match.
_KNOWN_SHEET_BUG_NAMES: Final = frozenset({"«Светящийся сахар»", "Абразив"})


def _load_price_totals() -> dict[str, int]:
    """`item name -> Итог, in kopecks`, from `shelter_prices.csv`.

    First occurrence wins on a duplicate name — matches
    `import_shelter.py`'s own `price_by_name` tie-break exactly, so this
    is always comparing against what was actually imported.
    """
    totals: dict[str, int] = {}
    with (_SNAPSHOT_DIR / "shelter_prices.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = row["name"].strip()
            total = row["total"]
            if name and total and total not in {"Низкий уровень", "Н/Д"} and name not in totals:
                totals[name] = int((Decimal(total) * 100).to_integral_value())
    return totals


async def test_shelter_import_matches_expected_volumes(shelter_only_db: CacheDb) -> None:
    connection = await shelter_only_db.connect()
    shelter_repo = ShelterRepository(connection)

    items = await shelter_repo.all_items()
    professions = await shelter_repo.get_professions()
    settings = await shelter_repo.get_settings()

    # §II.1: 421 named items in the plan's revision; the live book has grown
    # (423 here) the same way СКУПКА did — see Э0/Э4's status notes.
    assert len(items) >= 420
    assert len(professions) == 8
    assert professions["armor"] == 1  # §II.1 "Защитное снаряжение 1" — the one non-5 level
    assert settings["energy_price_kopeks"] == "86"  # 0.86 ₽


async def test_recipe_and_ingredient_counts_close_to_plan(shelter_only_db: CacheDb) -> None:
    connection = await shelter_only_db.connect()
    cursor = await connection.execute("SELECT COUNT(*) AS n FROM recipes")
    recipe_row = await cursor.fetchone()
    assert recipe_row is not None
    recipe_count = recipe_row["n"]
    cursor = await connection.execute("SELECT COUNT(*) AS n FROM recipe_ingredients")
    ingredient_row = await cursor.fetchone()
    assert ingredient_row is not None
    ingredient_count = ingredient_row["n"]

    assert recipe_count == 370  # exact match to §II.1's "Итого 370"
    # 1490 in the plan; a handful of rows are dropped here for being
    # genuinely malformed in the live sheet (blank qty/yield, or an
    # explicit qty=0 "Энергия" line) — see import_shelter.py's comments.
    assert 1480 <= ingredient_count <= 1490


async def test_bridge_covers_every_active_catalog_item(
    full_db: tuple[CacheDb, Any, Any],
) -> None:
    cache_db, _sheets_report, shelter_report = full_db
    connection = await cache_db.connect()
    catalog_repo = CatalogItemsRepository(connection)
    active_items = await catalog_repo.all()

    assert shelter_report.bridged_automatically + len(shelter_report.bridge_unmatched) == len(
        active_items
    )
    # §II.4: most of the ~199-name catalog bridges automatically; a real
    # minority needs the owner's manual confirmation (drifted names like
    # "Подорожник" vs "Ящик гранат «Подорожник»" — not a bug, expected).
    assert shelter_report.bridged_automatically > len(shelter_report.bridge_unmatched)


async def test_bridged_items_are_actually_set_on_catalog_items(
    full_db: tuple[CacheDb, Any, Any],
) -> None:
    cache_db, _sheets_report, shelter_report = full_db
    connection = await cache_db.connect()
    catalog_repo = CatalogItemsRepository(connection)

    bridged_count = 0
    for item in await catalog_repo.all():
        if item.name not in shelter_report.bridge_unmatched:
            assert item.shelter_item_id is not None, f"{item.name} should be bridged"
            bridged_count += 1
    assert bridged_count == shelter_report.bridged_automatically


async def test_level_e_leaf_item_costs_match_sheet_totals(shelter_only_db: CacheDb) -> None:
    """Level E (§VI.2), scoped to *leaf* items: every item resolved via
    `my_price`/`market` (no recipe involved at all) matches the sheet's own
    `Итог` column exactly, in kopecks.

    ★ Deliberately does **not** extend this zero-tolerance check to
    `crafted` items — see this module's docstring / Э5's status in
    sqlite_migration.md: a recipe's own "Цена 1 ед." column for an
    ingredient that is itself craftable is a value typed into that specific
    recipe row, not a live formula reference back to the ingredient's own
    `Итог` — the same kind of hand-maintained staleness the plan already
    documented elsewhere (e.g. §I.6's "Мякоть солевика", stale because
    `/sync_prices` hadn't run). `compute_costs()` recomputes every
    ingredient from first principles instead of trusting that cached
    per-recipe number, which is *more* correct, not a bug — but it means
    a `crafted` item's cost can legitimately differ from the sheet's own
    cached total by however stale that particular ingredient reference was.
    """
    connection = await shelter_only_db.connect()
    shelter_repo = ShelterRepository(connection)
    items = await shelter_repo.all_items()
    totals = _load_price_totals()

    mismatches = []
    checked = 0
    for item in items:
        if item.name in _AMBIGUOUS_DUPLICATE_PRICE_NAMES or item.name in _KNOWN_SHEET_BUG_NAMES:
            continue  # see the constants' docstrings — no trustworthy ground truth here
        expected = totals.get(item.name)
        if expected is None:
            continue  # no ground truth for this item (e.g. Энергия, an ingredient-only name)
        assert item.id is not None
        stored = await shelter_repo.get_cost(item.id)
        assert stored is not None
        if stored.source not in {"my_price", "market"}:
            continue  # crafted items: see the docstring above
        checked += 1
        if stored.cost_kopeks != expected:
            mismatches.append((item.name, expected, stored.cost_kopeks, stored.source, stored.note))

    assert checked > 100  # guards against a vacuous pass
    assert mismatches == []


async def test_crafted_item_costs_are_internally_consistent(shelter_only_db: CacheDb) -> None:
    """Every `crafted` item's stored cost must equal
    `ceil(Σ(qty × stored ingredient cost) / units_per_craft)` for its own
    `best_recipe_id` — proves `compute_costs()`'s arithmetic is self-
    consistent end-to-end on the real recipe graph (421 items, 370
    recipes), independent of whether the sheet's own cached total agrees
    (see `test_level_e_leaf_item_costs_match_sheet_totals`)."""
    from fractions import Fraction

    connection = await shelter_only_db.connect()
    shelter_repo = ShelterRepository(connection)
    items = await shelter_repo.all_items()
    specs_by_recipe_id = {
        s.recipe_id: s for s in await shelter_repo.load_recipe_specs_for_current_levels()
    }

    checked = 0
    for item in items:
        assert item.id is not None
        result = await shelter_repo.get_cost(item.id)
        assert result is not None
        if result.source != "crafted":
            continue
        assert result.best_recipe_id is not None
        spec = specs_by_recipe_id[result.best_recipe_id]
        total = Fraction(0)
        for ingredient_id, qty in spec.ingredients:
            ingredient_result = await shelter_repo.get_cost(ingredient_id)
            assert ingredient_result is not None
            assert ingredient_result.cost_kopeks is not None
            total += qty * ingredient_result.cost_kopeks
        expected = total / spec.units_per_craft
        expected_ceil = -(-expected.numerator // expected.denominator)
        assert result.cost_kopeks == expected_ceil, item.name
        checked += 1

    assert checked > 100  # guards against a vacuous pass


async def test_level_f_multi_recipe_item_resolves_via_one_of_its_recipes(
    shelter_only_db: CacheDb,
) -> None:
    """Level F (§VI.2): «Ковер» (5 recipes in the live data, §II.2) resolves
    via `crafted`, and the chosen `best_recipe_id` is genuinely one of its
    own recipes — the multi-recipe minimum-selection path runs on real
    data, not just the synthetic case in `test_cost.py`.

    Real "Ковер" recipes happen to be identical, so this doesn't exercise
    *which* one wins on cost — that's covered directly, with two recipes at
    different costs, by `test_compute_costs_multi_recipe_picks_the_cheaper_one`."""
    connection = await shelter_only_db.connect()
    shelter_repo = ShelterRepository(connection)
    items = await shelter_repo.get_items_by_name()

    kover = next(v for v in items.values() if v.name == "Ковер")
    assert kover.id is not None
    result = await shelter_repo.get_cost(kover.id)
    assert result is not None
    assert result.source == "crafted"

    cursor = await connection.execute(
        "SELECT id FROM recipes WHERE output_item_id = ?", (kover.id,)
    )
    kover_recipe_ids = {row["id"] async for row in cursor}
    assert result.best_recipe_id in kover_recipe_ids


async def test_level_g_low_level_recipes_are_unavailable(shelter_only_db: CacheDb) -> None:
    """Level G (§VI.2): «Защитное снаряжение» is level 1 — recipes needing
    a higher level ("Низкий уровень" in the sheet) must not be usable."""
    connection = await shelter_only_db.connect()
    shelter_repo = ShelterRepository(connection)
    specs = await shelter_repo.load_recipe_specs_for_current_levels()

    armor_specs = [s for s in specs if s.profession_key == "armor"]
    unavailable = [s for s in armor_specs if s.units_per_craft == 0]
    assert len(unavailable) >= 1  # §II.1: 7 recipes unavailable at level 1


async def test_level_h_antitoxin_cycle_does_not_crash_and_resolves(
    shelter_only_db: CacheDb,
) -> None:
    """Level H (§VI.2): «Антитоксин» (Медицина) has a self-referencing
    recipe (itself among its own ingredients) — resolving it must not blow
    the stack.

    On real data the cycle is never actually *walked*: «Антитоксин» has
    «Моя цена» = 1800 ₽ set (§V.2 rule 1 — a manual price short-circuits
    recipe lookup entirely, before the cyclic recipe is ever considered),
    so `source == 'my_price'` here, matching the sheet's own `total`
    exactly. The cycle-guard code path itself (no `RecursionError`, a
    `cycle:` note, graceful fallback) is exercised directly — with a
    synthetic self-only recipe and no price override — in
    `tests/unit/domain/shelter/test_cost.py`.
    """
    connection = await shelter_only_db.connect()
    shelter_repo = ShelterRepository(connection)
    items = await shelter_repo.get_items_by_name()
    totals = _load_price_totals()

    antitoxin = next(v for v in items.values() if v.name == "Антитоксин")
    assert antitoxin.id is not None
    assert antitoxin.my_kopeks == 180_000  # 1800.00 ₽ — the override that short-circuits the cycle
    result = await shelter_repo.get_cost(antitoxin.id)

    assert result is not None
    assert result.source == "my_price"
    assert result.cost_kopeks == totals["Антитоксин"] == 180_000
