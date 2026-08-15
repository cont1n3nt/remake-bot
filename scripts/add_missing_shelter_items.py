"""One-off: add 8 shelter items/recipes the original Э0 snapshot never captured.

Every recipe/price below was given by the owner in chat (sqlite_migration.md
Часть XIV.2 — the 6 previously-unbridgeable catalog items) plus two new
recipes for cost planning only. Lives in `scripts/`, not `src/` — one-shot
data entry, not part of the coverage denominator (sqlite_migration.md §XI).
Safe to delete after running.

What this adds:

1. Four pure market-priced components (bought from players, no recipe):
   Баллон с метаном/пропаном, Канистра с бензином/дизелем — bridged to
   their existing `catalog_items` rows.
2. Альфабиоматериал (сырье и материалы, ур.5): craftable, bridged to its
   existing `catalog_items` row.
3. Схрон мастера (инженерия, ур.5): craftable, bridged to its existing
   `catalog_items` row.
4. Экранированный схрон мастера: a recipe for an *existing* shelter item
   (id resolved by name at runtime) that already had no recipe at all —
   already bridged to `catalog_items` from Э5, nothing to bridge here.
5. Схрон ветерана / Экранированный схрон ветерана (инженерия, ур.5):
   craftable, for `/себестоимость`-style cost tracking only — owner
   confirmed these are real in-game recipes but must NOT be sellable
   through the bot, so deliberately *not* bridged to any `catalog_items`
   row and no catalog entry is created for them either.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from stalbot.domain.clock import SystemClock
from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name
from stalbot.infrastructure.cache.repositories.shelter import RecipeImport, ShelterRepository

logger = logging.getLogger(__name__)

_ENERGY_NAME_NORM = normalize_item_name("Энергия")


def _to_kopeks(rub: int) -> int:
    return int((Decimal(rub) * 100).to_integral_value())


#: (name, market_kopeks) — pure market-priced components, no recipe.
_NEW_COMPONENTS: tuple[tuple[str, int], ...] = (
    ("Баллон с метаном", _to_kopeks(2700)),
    ("Баллон с пропаном", _to_kopeks(2250)),
    ("Канистра с бензином", _to_kopeks(1800)),
    ("Канистра с дизелем", _to_kopeks(2250)),
)

#: New craftable shelter items this script inserts (recipes below reference
#: them by name_norm, same as the components above).
_NEW_CRAFTABLES: tuple[str, ...] = (
    "Альфабиоматериал",
    "Схрон мастера",
    "Схрон ветерана",
    "Экранированный схрон ветерана",
)

#: `(catalog item name, catalog category, shelter item name)` — bridged
#: after insertion. "Экранированный схрон мастера" already has a bridge
#: from Э5 (only its recipe was missing), so it's not listed here.
_CATALOG_BRIDGES: tuple[tuple[str, str, str], ...] = (
    ("Баллон с метаном", "resource", "Баллон с метаном"),
    ("Баллон с пропаном", "resource", "Баллон с пропаном"),
    ("Канистра с бензином", "resource", "Канистра с бензином"),
    ("Канистра с дизелем", "resource", "Канистра с дизелем"),
    ("Альфабиоматериал", "resource", "Альфабиоматериал"),
    ("Схрон мастера", "boost", "Схрон мастера"),
)

#: `(output name, profession, level, yield, [(ingredient name, qty), ...])`
#: — energy cost is folded in as an «Энергия» ingredient, matching how
#: every other recipe in this schema pays for it (§V.2).
_RECIPES: tuple[tuple[str, str, int, int, tuple[tuple[str, int], ...]], ...] = (
    (
        "Альфабиоматериал",
        "materials",
        5,
        16,
        (
            ("Селезенка мутанта из Любеча", 1),
            ("Глаз зомби", 1),
            ("Гипофиз мертвеца", 1),
            ("Энергия", 100),
        ),
    ),
    (
        "Схрон мастера",
        "engineering",
        5,
        1,
        (
            ("Железо", 3),
            ("Защитное снаряжение", 2),
            ("Полимеры", 10),
            ("Прочный металл", 1),
            ("Смазочные материалы", 1),
            ("Энергия", 900),
        ),
    ),
    (
        "Экранированный схрон мастера",
        "engineering",
        5,
        1,
        (
            ("Полимеры", 15),
            ("Аномальные материалы", 25),
            ("Прочный металл", 2),
            ("Листы сваренного металла", 3),
            ("Смазочные материалы", 1),
            ("Энергия", 900),
        ),
    ),
    (
        "Схрон ветерана",
        "engineering",
        5,
        1,
        (
            ("Сталь", 3),
            ("Железо", 3),
            ("Смазочные материалы", 1),
            ("Энергия", 500),
        ),
    ),
    (
        "Экранированный схрон ветерана",
        "engineering",
        5,
        1,
        (
            ("Железо", 8),
            ("Аномальные материалы", 20),
            ("Листы сваренного металла", 8),
            ("Крепкая сталь", 9),
            ("Смазочные материалы", 2),
            ("Энергия", 800),
        ),
    ),
)


async def run(cache_db: CacheDb) -> None:
    """Insert the new items/recipes and bridge what should be bridged.

    Args:
        cache_db: An already-connected-or-not `CacheDb` for the live cache.
    """
    connection = await cache_db.connect()
    shelter = ShelterRepository(connection)
    catalog = CatalogItemsRepository(connection)
    now = SystemClock().now()

    existing = await shelter.get_items_by_name()

    new_items = [
        ShelterItem(
            id=None,
            name=name,
            name_norm=normalize_item_name(name),
            kind="component",
            market_kopeks=market_kopeks,
            my_kopeks=None,
            vendor_kopeks=None,
            updated_at=now,
        )
        for name, market_kopeks in _NEW_COMPONENTS
        if normalize_item_name(name) not in existing
    ] + [
        ShelterItem(
            id=None,
            name=name,
            name_norm=normalize_item_name(name),
            kind="craftable",
            market_kopeks=None,
            my_kopeks=None,
            vendor_kopeks=None,
            updated_at=now,
        )
        for name in _NEW_CRAFTABLES
        if normalize_item_name(name) not in existing
    ]
    if new_items:
        await shelter.insert_items(new_items)
        logger.info("Новых предметов убежки: %d", len(new_items))
    else:
        logger.info("Все предметы убежки уже существуют — пропускаю insert_items")

    item_ids: dict[str, int] = {}
    for name_norm, item in (await shelter.get_items_by_name()).items():
        assert item.id is not None  # noqa: S101 - a fetched item always has a persisted id
        item_ids[name_norm] = item.id

    recipe_imports = [
        RecipeImport(
            output_name_norm=normalize_item_name(output_name),
            profession_key=profession,
            source_sheet=None,
            source_cell=None,
            ingredients=tuple(
                (normalize_item_name(ing_name), Fraction(qty)) for ing_name, qty in ingredients
            ),
            yields_by_level={level: Fraction(yield_)},
        )
        for output_name, profession, level, yield_, ingredients in _RECIPES
    ]
    await shelter.insert_recipes(recipe_imports, item_ids)
    logger.info("Новых рецептов: %d", len(recipe_imports))

    bridged = 0
    for catalog_name, category_value, shelter_name in _CATALOG_BRIDGES:
        catalog_item = await catalog.find(
            normalize_item_name(catalog_name), ItemCategory(category_value)
        )
        shelter_id = item_ids.get(normalize_item_name(shelter_name))
        if catalog_item is None or catalog_item.id is None or shelter_id is None:
            logger.warning(
                "Мост не применён: catalog=%r shelter=%r (одно из двух не найдено)",
                catalog_name,
                shelter_name,
            )
            continue
        await catalog.set_shelter_item_id(catalog_item.id, shelter_id, now=now)
        bridged += 1
    logger.info("Мостов проставлено: %d/%d", bridged, len(_CATALOG_BRIDGES))


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    await run(cache_db)
    await cache_db.close()


if __name__ == "__main__":
    asyncio.run(main())
