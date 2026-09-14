"""Tests for `scripts/seed_shop_assortment.py` (заявка 13.09.2026 п.2)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.shop import ShopRepository

_MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "seed_shop_assortment.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


seed_mod = _load_module("seed_shop_assortment", _MODULE_PATH)


async def test_run_seeds_all_four_categories_and_twelve_items(tmp_path: Path) -> None:
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    try:
        report = await seed_mod.run(cache_db)

        assert report.categories == 4
        assert report.items_added == 12
        assert report.items_skipped == ()

        connection = await cache_db.connect()
        shop = ShopRepository(connection)
        categories = await shop.categories(include_inactive=True)
        items = await shop.items(include_hidden=True)
        assert {c.key for c in categories} == {"small", "medium", "large", "elite"}
        assert len(items) == 12
        assert {item.price_coins for item in items} == {
            15,
            20,
            35,
            50,
            65,
            75,
            90,
            105,
            120,
            150,
            175,
            200,
        }
    finally:
        await cache_db.close()


async def test_run_is_idempotent(tmp_path: Path) -> None:
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    try:
        await seed_mod.run(cache_db)
        second = await seed_mod.run(cache_db)

        assert second.items_added == 0
        assert len(second.items_skipped) == 12

        connection = await cache_db.connect()
        shop = ShopRepository(connection)
        items = await shop.items(include_hidden=True)
        assert len(items) == 12  # not doubled
    finally:
        await cache_db.close()
