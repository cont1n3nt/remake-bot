"""One-time importer: убежка `shelter_*.csv` (Э0) -> shelter schema (Э5).

sqlite_migration.md §VII (Э5). Lives in `scripts/`, not `src/` (§XI).

What this does, in order:

1. `shelter_settings.csv` -> `shelter_settings` (energy price, sale bonus,
   count-ingredient-sale) and `professions` (8 rows, current level each).
2. `shelter_prices.csv` (9 blocks) + recipe outputs/ingredients + the
   pseudo-ingredient «Энергия» -> the full 421-ish `shelter_items` name
   registry, each with `market_kopeks`/`my_kopeks`.
3. `shelter_recipes.csv` -> `recipes` + `recipe_ingredients` +
   `recipe_yields` (one level per recipe — the level the sheet was set to
   at export time; see the module docstring's "known limitation" below).
4. Bridge: `catalog_items.shelter_item_id`, matched by an aggressively
   normalized name (NFKC -> lower -> ё/е -> strip «»"' -> collapse spaces,
   §V.1) — everything that doesn't match automatically is reported for
   manual confirmation (§II.4: expected ~28 of ~199).
5. `domain.shelter.cost.compute_costs()` over the whole graph, persisted
   to `shelter_cost`.

★ Known limitation: `recipe_yields` only gets the *current* level's
`units_per_craft` per recipe (from `shelter_recipes.csv`, itself accurate
because it was captured from the live sheet at its current level and
already validated against the sheet's own computed costs — see Э5's
status in sqlite_migration.md). `shelter_tech_yields.csv` has the full
1-5 curve, but keyed by item *name*, not by recipe — for the 27 items
with several recipes there is no unambiguous way to tell which recipe a
given yield row belongs to without more sheet inspection than Э0 captured.
Changing a profession's level away from what was imported will therefore
make recipes at that profession count as unavailable (`units_per_craft`
falls back to 0) until a future pass fills in the other 4 levels
per-recipe, not just per-name.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Final

from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.shelter.cost import compute_costs
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name
from stalbot.infrastructure.cache.repositories.shelter import RecipeImport, ShelterRepository

logger = logging.getLogger(__name__)

_ENERGY_NAME: Final = "Энергия"

#: §II.1 "Инструкция" — profession name (as written in the sheet) -> the
#: stable key used everywhere else in the schema (§IV.3).
_PROFESSION_KEYS: Final[dict[str, str]] = {
    "Боеприпасы": "ammo",
    "Пиротехника": "pyro",
    "Защитное снаряжение": "armor",
    "Инженерия": "engineering",
    "Кулинария": "cooking",
    "Самогоноварение": "moonshine",
    "Медицина": "medicine",
    "Сырье и материалы": "materials",
}

#: `Цены` block index (§I.1/Э0) -> profession key. Block 0 ("Основные
#: компоненты") has no profession — it's the base-component block.
_PRICE_BLOCK_PROFESSION: Final[dict[int, str]] = {
    1: "ammo",
    2: "pyro",
    3: "armor",
    4: "engineering",
    5: "cooking",
    6: "moonshine",
    7: "medicine",
    8: "materials",
}


def _to_kopeks(value: str) -> int | None:
    """Parse a ruble-denominated CSV cell into whole kopecks.

    Blank, and the sheet's own "Низкий уровень"/"Н/Д" sentinels (§II.1: a
    recipe unavailable at the current level, or missing bonus-yield data)
    both mean "no number here" — not a parse error.
    """
    if not value or value in {"Низкий уровень", "Н/Д"}:
        return None
    return int((Decimal(value) * 100).to_integral_value())


def _bridge_key(name: str) -> str:
    """Aggressive normalization for catalog<->shelter name matching (§V.1).

    NFKC -> lower -> ё/е -> strip «»"' -> collapse whitespace. More
    aggressive than `normalize_item_name`/`shelter_items.name_norm`
    (which stay simple/lossless) — this key exists only to bridge two
    catalogs that were typed by hand at different times with different
    quoting conventions ("Ящик гранат «Подорожник»" vs "Подорожник").
    """
    text = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    text = re.sub(r"[«»\"'«»]", "", text)
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class _PriceRow:
    name: str
    market_kopeks: int | None
    my_kopeks: int | None
    profession_key: str | None
    """`None` for a block-0 base component."""


def _load_settings(path: Path) -> tuple[dict[str, str], dict[str, int]]:
    """Parse `Инструкция` into `(shelter_settings values, profession levels)`."""
    settings: dict[str, str] = {}
    levels: dict[str, int] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key, value = row["key"], row["value"]
            if key in _PROFESSION_KEYS:
                levels[_PROFESSION_KEYS[key]] = int(float(value))
            elif key == "Учитывать продажу ингредиентов":
                settings["count_ingredient_sale"] = "1" if value == "True" else "0"
            elif key == "Бонус к продаже, %":
                settings["sale_bonus_pct"] = str(Decimal(value))
            elif key == "Цена 1 ед. энергии, руб":
                kopeks = _to_kopeks(value)
                assert kopeks is not None  # noqa: S101 - the energy price cell is never blank
                settings["energy_price_kopeks"] = str(kopeks)
    return settings, levels


def _load_prices(path: Path) -> list[_PriceRow]:
    rows: list[_PriceRow] = []
    with path.open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            name = raw["name"].strip()
            if not name:
                continue
            block = int(raw["block"])
            rows.append(
                _PriceRow(
                    name=name,
                    market_kopeks=_to_kopeks(raw["value1"]),
                    my_kopeks=_to_kopeks(raw["my_price"]),
                    profession_key=_PRICE_BLOCK_PROFESSION.get(block),
                )
            )
    return rows


@dataclass(frozen=True, slots=True)
class _RecipeRow:
    profession: str
    output: str
    recipe_seq: int
    units_per_craft: Fraction
    ingredients: list[tuple[str, Fraction]]


def _load_recipes(path: Path) -> list[_RecipeRow]:
    grouped: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            key = (raw["profession"], raw["output"], int(raw["recipe_seq"]))
            grouped[key].append(raw)

    recipes: list[_RecipeRow] = []
    for (profession, output, seq), rows in grouped.items():
        rows.sort(key=lambda r: int(r["position"]))
        # A handful of recipe rows in the live sheet are genuinely
        # incomplete (e.g. "Экранированный схрон мастера": an ingredient
        # listed with no quantity/yield at all) — treat a blank
        # units_per_craft the same as an explicit 0 ("Низкий уровень":
        # unavailable), not a parse error.
        units_text = next((r["units_per_craft"] for r in rows if r["units_per_craft"]), "")
        units_per_craft = Fraction(units_text) if units_text else Fraction(0)
        ingredients = [
            (r["ingredient"].strip(), Fraction(r["qty"]))
            for r in rows
            if int(r["position"]) > 0
            and r["ingredient"].strip()
            and r["qty"]
            and Fraction(r["qty"]) > 0  # a handful of rows list e.g. "Энергия" at qty=0
        ]
        recipes.append(
            _RecipeRow(
                profession=profession,
                output=output,
                recipe_seq=seq,
                units_per_craft=units_per_craft,
                ingredients=ingredients,
            )
        )
    return recipes


def _all_item_names(prices: Iterable[_PriceRow], recipes: Iterable[_RecipeRow]) -> set[str]:
    names = {p.name for p in prices}
    for recipe in recipes:
        names.add(recipe.output)
        names.update(ingredient for ingredient, _qty in recipe.ingredients)
    names.add(_ENERGY_NAME)
    return names


@dataclass(frozen=True, slots=True)
class ShelterImportReport:
    """Summary counts printed after a run."""

    items_imported: int
    recipes_imported: int
    ingredients_imported: int
    professions_set: int
    bridged_automatically: int
    bridge_unmatched: tuple[str, ...]
    """Active catalog item names that need manual `shelter_item_id` confirmation."""
    costs_computed: int
    costs_unresolved: int


async def run(snapshot_dir: Path, cache_db: CacheDb, *, now: datetime) -> ShelterImportReport:
    """Run the full shelter import against an already-connected `CacheDb`.

    Args:
        snapshot_dir: An Э0 snapshot directory.
        cache_db: A `CacheDb` — `connect()` may or may not have run yet.
        now: Timestamp stamped on every created/updated row.
    """
    connection = await cache_db.connect()
    shelter_repo = ShelterRepository(connection)
    catalog_repo = CatalogItemsRepository(connection)

    settings, levels = _load_settings(snapshot_dir / "shelter_settings.csv")
    prices = _load_prices(snapshot_dir / "shelter_prices.csv")
    recipes = _load_recipes(snapshot_dir / "shelter_recipes.csv")

    price_by_name: dict[str, _PriceRow] = {}
    for price in prices:
        price_by_name.setdefault(price.name, price)  # first block wins on a name clash

    # --- shelter_items ---
    all_names = _all_item_names(prices, recipes)
    craftable_names = {r.output for r in recipes} | {
        p.name for p in prices if p.profession_key is not None
    }
    items: list[ShelterItem] = []
    for name in sorted(all_names):
        if name == _ENERGY_NAME:
            energy_kopeks = int(settings["energy_price_kopeks"])
            items.append(
                ShelterItem(
                    id=None,
                    name=name,
                    name_norm=normalize_item_name(name),
                    kind="virtual",
                    market_kopeks=energy_kopeks,
                    my_kopeks=None,
                    vendor_kopeks=None,
                )
            )
            continue
        price_row = price_by_name.get(name)
        kind = "craftable" if name in craftable_names else "component"
        items.append(
            ShelterItem(
                id=None,
                name=name,
                name_norm=normalize_item_name(name),
                kind=kind,
                market_kopeks=price_row.market_kopeks if price_row else None,
                my_kopeks=price_row.my_kopeks if price_row else None,
                vendor_kopeks=None,
            )
        )
    item_ids = await shelter_repo.insert_items(items)

    # --- professions ---
    await shelter_repo.set_professions(
        {key: (name, levels.get(key, 1)) for name, key in _PROFESSION_KEYS.items()}
    )
    await shelter_repo.set_settings(settings)

    # --- recipes ---
    # `r.profession` (from shelter_recipes.csv's "profession" column) is
    # already the stable key (ammo|pyro|...) — Э0's exporter wrote it that
    # way, matching `professions.key` directly. No name translation needed
    # here; `_PROFESSION_KEYS` is only for the Russian names in
    # `Инструкция` (§II.1's settings sheet, a different source file).
    recipe_imports = [
        RecipeImport(
            output_name_norm=normalize_item_name(r.output),
            profession_key=r.profession,
            source_sheet=r.profession,
            source_cell=None,
            ingredients=tuple((normalize_item_name(name), qty) for name, qty in r.ingredients),
            yields_by_level={levels.get(r.profession, 1): r.units_per_craft},
        )
        for r in recipes
    ]
    name_norm_to_id = {name_norm: item_id for name_norm, item_id in item_ids.items()}
    await shelter_repo.insert_recipes(recipe_imports, name_norm_to_id)
    ingredient_count = sum(len(r.ingredients) for r in recipe_imports)

    # --- bridge: catalog_items.shelter_item_id ---
    shelter_by_bridge_key: dict[str, list[int]] = defaultdict(list)
    for item in items:
        shelter_by_bridge_key[_bridge_key(item.name)].append(item_ids[item.name_norm])

    catalog_items = await catalog_repo.all()
    bridged = 0
    unmatched: list[str] = []
    for catalog_item in catalog_items:
        assert catalog_item.id is not None  # noqa: S101 - persisted rows always have an id
        candidates = shelter_by_bridge_key.get(_bridge_key(catalog_item.name), [])
        if len(candidates) == 1:
            await catalog_repo.set_shelter_item_id(catalog_item.id, candidates[0], now=now)
            bridged += 1
        else:
            unmatched.append(catalog_item.name)

    # --- cost computation ---
    item_specs = await shelter_repo.load_item_specs()
    recipe_specs = await shelter_repo.load_recipe_specs_for_current_levels()
    results = compute_costs(item_specs, recipe_specs)
    await shelter_repo.save_costs(results, now=now)
    unresolved = sum(1 for r in results.values() if r.source == "unresolved")

    return ShelterImportReport(
        items_imported=len(items),
        recipes_imported=len(recipe_imports),
        ingredients_imported=ingredient_count,
        professions_set=len(_PROFESSION_KEYS),
        bridged_automatically=bridged,
        bridge_unmatched=tuple(unmatched),
        costs_computed=len(results),
        costs_unresolved=unresolved,
    )


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    report = await run(args.snapshot_dir, cache_db, now=datetime.now(UTC))
    await cache_db.close()

    logger.info("Предметов убежки импортировано: %d", report.items_imported)
    logger.info(
        "Рецептов: %d, ингредиентов: %d", report.recipes_imported, report.ingredients_imported
    )
    logger.info("Профессий выставлено: %d", report.professions_set)
    logger.info(
        "Мост автосопоставлен: %d, требует ручного подтверждения: %d",
        report.bridged_automatically,
        len(report.bridge_unmatched),
    )
    if report.bridge_unmatched:
        logger.info("Несопоставленные: %s", ", ".join(report.bridge_unmatched))
    logger.info(
        "Себестоимость посчитана для %d предметов (unresolved: %d)",
        report.costs_computed,
        report.costs_unresolved,
    )


if __name__ == "__main__":
    asyncio.run(main())
