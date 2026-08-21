"""One-off: bridge 20 catalog rows to their убежка counterparts by owner-confirmed name pairs.

`import_shelter.py`'s exact-name bridge (`_bridge_key`) doesn't catch
cross-system synonyms (sqlite_migration.md Часть XIV.2) — every pair below
was confirmed by the owner in chat on 2026-08-15. Two
categories of item are deliberately *not* included:

- `Граната "Кустарник-1"` (catalog, resource) — the убежка counterpart
  (`Ящик гранат «Кустарник-1»`) is a box of 10; the catalog item is priced
  per single grenade. Bridging 1:1 would show 10x the real crafting cost.
  Needs a per-catalog-item yield divisor, which the schema doesn't have yet
  — deferred to Э13 when `/себестоимость` is actually built.
- `Сумка СБП` / `Сумка СЭП` (catalog, boost) — the owner wants these to
  average two убежка variants (9мм/10мм) rather than pick one. The schema
  only supports a single `shelter_item_id` FK — also deferred to Э13.

Lives in `scripts/`, not `src/` — one-shot correction code, not part of the
coverage denominator (sqlite_migration.md §XI). Safe to delete after running.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from stalbot.domain.clock import SystemClock
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name

logger = logging.getLogger(__name__)

#: (catalog item name, catalog category, убежка shelter_items.name) — every
#: entry confirmed by the owner in chat on 2026-08-15.
_CONFIRMED_BRIDGES: tuple[tuple[str, ItemCategory, str], ...] = (
    ("Вонючка", ItemCategory.BOOST, "Ящик гранат «Вонючка»"),
    ("Гром", ItemCategory.BOOST, "Ящик гранат «Гром»"),
    ("Завеса", ItemCategory.BOOST, "Ящик гранат «Завеса»"),
    ("Искра", ItemCategory.BOOST, "Ящик гранат «Искра»"),
    ("Напалм", ItemCategory.BOOST, "Ящик гранат «Напалм»"),
    ("Подорожник", ItemCategory.BOOST, "Ящик гранат «Подорожник»"),
    ("Изумруд минералы", ItemCategory.RESOURCE, "Изумрудные минералы"),
    ("Жаркое из мутанта", ItemCategory.RESOURCE, "Жаркое из мутантов"),
    ("Мутировавшие фрагменты", ItemCategory.RESOURCE, "Мутировавшие ферменты"),
    ("Сумка 12.7 Easy Mode", ItemCategory.BOOST, "Сумка 12.7 мм Easy mode"),
    ("Сумка 5.56 Прогрев", ItemCategory.BOOST, "Сумка 5.56 мм «Прогрев»"),
    ("Сумка с апт проводника", ItemCategory.BOOST, "Подсумок с аптечками проводника"),
    ("Батарейка", ItemCategory.RESOURCE, "Энергетик «Батарейка»"),
    ("Батарейка", ItemCategory.BOOST, "Энергетик «Батарейка»"),
    ("Гейзер", ItemCategory.RESOURCE, "Водка «Гейзер»"),
    ("Гейзер", ItemCategory.BOOST, "Водка «Гейзер»"),
    ("Незабываемый", ItemCategory.RESOURCE, "Коктейль «Незабываемый»"),
    ("Незабываемый", ItemCategory.BOOST, "Коктейль «Незабываемый»"),
    ("Уха", ItemCategory.RESOURCE, "Уха из сома «Дворянская»"),
    ("Уха", ItemCategory.BOOST, "Уха из сома «Дворянская»"),
)


async def run(cache_db: CacheDb) -> tuple[int, list[str]]:
    """Apply every confirmed bridge; return (applied count, problems).

    Args:
        cache_db: An already-connected-or-not `CacheDb` for the live cache.
    """
    connection = await cache_db.connect()
    catalog = CatalogItemsRepository(connection)
    now = SystemClock().now()

    problems: list[str] = []
    applied = 0
    for catalog_name, category, shelter_name in _CONFIRMED_BRIDGES:
        catalog_item = await catalog.find(normalize_item_name(catalog_name), category)
        if catalog_item is None:
            problems.append(f"каталог: не найден «{catalog_name}» ({category.value})")
            continue
        assert catalog_item.id is not None  # noqa: S101 - a fetched item always has a persisted id

        cursor = await connection.execute(
            "SELECT id FROM shelter_items WHERE name = ?", (shelter_name,)
        )
        rows = list(await cursor.fetchall())
        if len(rows) != 1:
            problems.append(
                f"убежка: {'не найдено' if not rows else 'неоднозначно'} «{shelter_name}» "
                f"(для «{catalog_name}», {category.value})"
            )
            continue

        await catalog.set_shelter_item_id(catalog_item.id, rows[0]["id"], now=now)
        applied += 1
        logger.info("«%s» (%s) -> «%s»", catalog_name, category.value, shelter_name)

    return applied, problems


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    applied, problems = await run(cache_db)
    await cache_db.close()

    logger.info("Применено: %d/%d", applied, len(_CONFIRMED_BRIDGES))
    if problems:
        logger.warning("Проблемы (%d) — ничего не применено для этих строк:", len(problems))
        for problem in problems:
            logger.warning("  %s", problem)


if __name__ == "__main__":
    asyncio.run(main())
