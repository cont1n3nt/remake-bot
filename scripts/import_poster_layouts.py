"""One-off: move the frozen poster layout JSON into the database (заявка 13.09.2026 п.6).

Reads the three `layout_*.json` files that shipped inside the package,
writes them into `poster_sections`/`poster_slots` (migration 0011), and
copies every referenced icon into the configured poster-icons directory,
where uploads from `/poster_item` will join them.

Run once per deployment, after migration 0011 has been applied. Re-running
refuses by default (the tables would double up); `--replace` wipes both
tables first, which is the supported way to redo the import after fixing
something in the JSON.

Nothing here deletes the JSON: it stays in the repo as this script's
source, and as the record of what the layout looked like before the move.

Lives in `scripts/`, not `src/` — one-shot migration code, not part of the
coverage denominator (sqlite_migration.md §XI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from stalbot.domain.clock import SystemClock
from stalbot.domain.entities.poster_layout import PosterSectionRow, PosterSlotRow
from stalbot.domain.enums import ItemCategory, PosterKind
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.poster_layout import PosterLayoutRepository

logger = logging.getLogger(__name__)

#: Which catalog category each poster prices against — the same mapping
#: `PosterService` uses, duplicated here rather than imported so the script
#: keeps working if that module is refactored around it.
_CATEGORY_BY_KIND: dict[PosterKind, ItemCategory] = {
    PosterKind.RESOURCES: ItemCategory.RESOURCE,
    PosterKind.BOOSTS: ItemCategory.BOOST,
    PosterKind.BOOST_PURCHASES: ItemCategory.RESOURCE,
}


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What one run did."""

    sections: int
    slots: int
    bound: int
    """Slots resolved to a live `catalog_items.id`."""
    unbound: int
    """Slots kept by `name_norm` only — the catalog has no such item today."""
    icons_copied: int
    icons_missing: tuple[str, ...]


def _assets_dir() -> Path:
    return Path(str(resources.files("stalbot") / "assets" / "posters"))


def _load_layout(kind: PosterKind) -> dict[str, list[dict[str, Any]]]:
    text = (_assets_dir() / f"layout_{kind.value}.json").read_text(encoding="utf-8")
    result: dict[str, list[dict[str, Any]]] = json.loads(text)
    return result


async def _already_imported(layout: PosterLayoutRepository) -> bool:
    for kind in PosterKind:
        if await layout.sections(kind):
            return True
    return False


async def _wipe(cache_db: CacheDb) -> None:
    connection = await cache_db.connect()
    # `poster_slots` cascades off `poster_sections`, but deleting explicitly
    # keeps this readable and independent of the FK pragma being on.
    await connection.execute("DELETE FROM poster_slots")
    await connection.execute("DELETE FROM poster_sections")
    await connection.commit()


async def run(cache_db: CacheDb, *, icons_dir: Path, replace: bool = False) -> ImportReport:
    """Import all three layouts and their icons.

    Args:
        cache_db: An already-connected-or-not `CacheDb` for the live cache.
        icons_dir: Where to copy icons to (`Settings.poster_icons_dir`).
        replace: Wipe `poster_sections`/`poster_slots` first.

    Raises:
        RuntimeError: The layout is already imported and `replace` is not set.
    """
    connection = await cache_db.connect()
    layout_repo = PosterLayoutRepository(connection)
    catalog = CatalogItemsRepository(connection)

    if await _already_imported(layout_repo):
        if not replace:
            raise RuntimeError(
                "poster layout is already imported — re-run with --replace to redo it"
            )
        await _wipe(cache_db)

    catalog_by_key = {
        (item.name_norm, item.category): item
        for item in await catalog.all()
        if item.deleted_at is None
    }

    icons_dir.mkdir(parents=True, exist_ok=True)
    now = SystemClock().now()
    sections = slots = bound = unbound = icons_copied = 0
    missing: list[str] = []

    for kind in PosterKind:
        category = _CATEGORY_BY_KIND[kind]
        for section_order, section in enumerate(_load_layout(kind)["sections"]):
            section_id = await layout_repo.add_section(
                PosterSectionRow(
                    id=None,
                    poster_kind=kind,
                    name=section["name"],
                    sort_order=section_order,
                    columns=int(section.get("columns", 1)),
                ),
                now=now,
            )
            sections += 1

            for slot_order, entry in enumerate(section["items"]):
                icon_file = Path(entry["icon"]).name
                source = _assets_dir() / entry["icon"]
                destination = icons_dir / icon_file
                if source.is_file():
                    if not destination.exists():
                        shutil.copy2(source, destination)
                        icons_copied += 1
                else:
                    missing.append(f"{kind.value}: {entry['name']} -> {entry['icon']}")

                item = catalog_by_key.get((entry["name_norm"], category))
                if item is not None:
                    bound += 1
                else:
                    unbound += 1
                await layout_repo.add_slot(
                    PosterSlotRow(
                        id=None,
                        section_id=section_id,
                        sort_order=slot_order,
                        catalog_item_id=item.id if item is not None else None,
                        display_name=entry["name"],
                        name_norm=entry["name_norm"],
                        icon_file=icon_file,
                    ),
                    now=now,
                )
                slots += 1

    return ImportReport(
        sections=sections,
        slots=slots,
        bound=bound,
        unbound=unbound,
        icons_copied=icons_copied,
        icons_missing=tuple(missing),
    )


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--icons-dir", type=Path, default=Path("./data/poster_icons"))
    parser.add_argument(
        "--replace",
        action="store_true",
        help="wipe poster_sections/poster_slots before importing",
    )
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    report = await run(cache_db, icons_dir=args.icons_dir, replace=args.replace)
    await cache_db.close()

    logger.info(
        "Импортировано: секций %d, позиций %d (привязано к каталогу %d, только по имени %d).",
        report.sections,
        report.slots,
        report.bound,
        report.unbound,
    )
    logger.info("Иконок скопировано: %d -> %s", report.icons_copied, args.icons_dir)
    if report.icons_missing:
        logger.warning(
            "Не найдено файлов иконок: %d",  # noqa: RUF001
            len(report.icons_missing),
        )
        for entry in report.icons_missing:
            logger.warning("  %s", entry)


if __name__ == "__main__":
    asyncio.run(main())
