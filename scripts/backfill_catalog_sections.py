"""One-off: backfill `catalog_items.section` for boosts from `layout_boosts.json`.

заявка 21.08.2026 п.2: the order-boosts picker (`BoostOrderService.list_available_items`)
groups items by `CATALOG_SECTION_ORDER`, same as the boost posters — but
`section` was only ever populated for 4 of ~7 boost groups by the original
Sheets importer (`import_from_sheets.py`'s `_BOOST_GROUP_SECTIONS`,
sqlite_migration.md §IV.2). `layout_boosts.json` (Э11's poster extraction)
already carries a *complete*, hand-verified `name_norm -> section` mapping
for every boost — reusing it here fills the gap without re-deriving
anything from the source spreadsheet.

Only touches `catalog_items` with `category = 'boost'` and a `name_norm`
that appears in the layout; anything else (resources, a boost added after
the layout was extracted) is left untouched — the picker's sort already
puts an unmapped item last, not first, so leaving it alone is safe.

Lives in `scripts/`, not `src/` — one-shot maintenance, not part of the
coverage denominator (sqlite_migration.md §XI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from stalbot.domain.clock import SystemClock
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository

logger = logging.getLogger(__name__)

_LAYOUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "stalbot"
    / "assets"
    / "posters"
    / "layout_boosts.json"
)


def _load_section_by_name_norm(layout_path: Path) -> dict[str, str]:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for section in layout["sections"]:
        name = section.get("name")
        if not name:
            continue
        for entry in section["items"]:
            mapping[entry["name_norm"]] = name
    return mapping


async def run(cache_db: CacheDb, *, layout_path: Path = _LAYOUT_PATH) -> tuple[int, int]:
    """Backfill `section` for every boost whose `name_norm` is in the layout.

    Args:
        cache_db: An already-connected-or-not `CacheDb` for the live cache.
        layout_path: Override for tests — defaults to the committed layout.

    Returns:
        `(updated_count, already_set_count)`.
    """
    connection = await cache_db.connect()
    items = CatalogItemsRepository(connection)
    section_by_name_norm = _load_section_by_name_norm(layout_path)

    updated = 0
    already_set = 0
    now = SystemClock().now()
    for item in await items.by_category(ItemCategory.BOOST):
        if item.deleted_at is not None or item.id is None:
            continue
        section = section_by_name_norm.get(item.name_norm)
        if section is None:
            continue
        if item.section == section:
            already_set += 1
            continue
        await items.set_section(item.id, section, now=now)
        updated += 1
    return updated, already_set


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    updated, already_set = await run(cache_db)
    await cache_db.close()

    logger.info(
        "Секции проставлены: %d обновлено, %d уже совпадали", updated, already_set
    )


if __name__ == "__main__":
    asyncio.run(main())
