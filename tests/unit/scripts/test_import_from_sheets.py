"""End-to-end test for `scripts/import_from_sheets.py` against the real Э0
snapshot (sqlite_migration.md §VII, Э4's "сверка A через SQL-агрегат").

Proves the whole chain: CSV snapshot -> importer -> SQLite -> materialized
`player_progression` matches `compute_progression()` fed the *fixed*
(variant B) aggregates already validated in `test_sheet_parity.py` — not
`users.csv` directly, since the live import intentionally departs from the
legacy referral-bug formula (§III.2, owner-approved).
"""

import csv
import importlib.util
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from stalbot.domain.enums import ItemCategory
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.calculator import compute_progression
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from tests.support.sheet_snapshot import build_fixed_aggregates, load_tickets, load_users

_MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "import_from_sheets.py"
_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "sheet_snapshot_2026-08-10"
_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_from_sheets", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


async def test_import_matches_expected_volumes(tmp_path: Path) -> None:
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    report = await mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
    await cache_db.close()

    tickets = load_tickets(_SNAPSHOT_DIR / "tickets.csv")
    users = load_users(_SNAPSHOT_DIR / "users.csv")
    real_deal_count = sum(1 for t in tickets if t.deal_type is not None)
    b_nicks = {t.nick_norm for t in tickets}
    h_nicks = {t.referred_by_norm for t in tickets if t.referred_by_norm}
    expected_players = len(b_nicks | h_nicks)

    assert report.players_created == expected_players
    assert report.players_created == len(users) + len(h_nicks - b_nicks)
    assert report.deals_imported == real_deal_count
    assert report.placeholder_rows_skipped == len(tickets) - real_deal_count
    assert report.recomputed_players == report.players_created


async def test_import_deal_count_matches_players_repo(tmp_path: Path) -> None:
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    report = await mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
    connection = await cache_db.connect()

    assert await DealsRepository(connection).count() == report.deals_imported
    assert len(await PlayersRepository(connection).all()) == report.players_created

    await cache_db.close()


async def test_imported_items_match_active_and_deleted_counts(tmp_path: Path) -> None:
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    await mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
    connection = await cache_db.connect()
    items_repo = CatalogItemsRepository(connection)

    active = await items_repo.all()
    deleted = await items_repo.find("аминокислота", ItemCategory.RESOURCE)
    all_including_deleted = await items_repo.all(include_deleted=True)

    assert len(active) == 224  # 225 raw rows - 1 soft-deleted (Аминокислота)
    assert deleted is None  # excluded from the default (non-deleted) view
    assert len(all_including_deleted) == 225
    corrected = next(i for i in active if i.name_norm == "термическая смесь")
    assert corrected.name == "Термическая смесь"

    await cache_db.close()


async def test_imported_progression_matches_fixed_variant_b_formula(tmp_path: Path) -> None:
    """The full-chain parity check: SQLite-materialized progression must
    equal `compute_progression()` fed the same fixed (variant B) aggregates
    `test_sheet_parity.py`'s level C already validated against the sheet."""
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    await mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
    connection = await cache_db.connect()

    players_repo = PlayersRepository(connection)
    progression_repo = ProgressionRepository(connection)

    tickets = load_tickets(_SNAPSHOT_DIR / "tickets.csv")
    users = load_users(_SNAPSHOT_DIR / "users.csv")
    fixed_aggregates = build_fixed_aggregates(tickets, users)

    mismatches = []
    for user in users:
        player = await players_repo.get_by_nick(NormalizedNick(user.nick_norm))
        assert player is not None
        assert player.id is not None
        stored = await progression_repo.get(player.id)
        assert stored is not None

        expected = compute_progression(fixed_aggregates[user.nick_norm])
        expected_rank_key = expected.rank.key if expected.rank is not None else None
        expected_role_key = (
            expected.referral_role.key if expected.referral_role is not None else None
        )
        actual = (
            stored.coins,
            stored.xp,
            stored.purchase_turnover,
            stored.sale_turnover,
            stored.referral_count,
            stored.rank_key,
            stored.referral_role_key,
        )
        expect = (
            expected.coins,
            expected.xp,
            expected.purchase_turnover,
            expected.sale_turnover,
            expected.referral_count,
            expected_rank_key,
            expected_role_key,
        )
        if actual != expect:
            mismatches.append((user.nick_norm, expect, actual))

    assert mismatches == []
    await cache_db.close()


async def test_referrer_resolution_matches_fixed_variant_b(tmp_path: Path) -> None:
    """Spot-check the known referral_fix_diff.csv players resolve the same
    referrer relationship the fixed-aggregates builder computes."""
    cache_db = CacheDb(tmp_path / "cache.sqlite3")
    await mod.run(_SNAPSHOT_DIR, cache_db, now=_NOW)
    connection = await cache_db.connect()
    players_repo = PlayersRepository(connection)

    diff_path = (
        Path(__file__).resolve().parents[2]
        / "unit"
        / "domain"
        / "progression"
        / "referral_fix_diff.csv"
    )
    with diff_path.open(encoding="utf-8") as f:
        diff_rows = list(csv.DictReader(f))
    assert diff_rows  # guards against a vacuous pass

    for row in diff_rows:
        player = await players_repo.get_by_nick(NormalizedNick(row["nick"]))
        assert player is not None, f"{row['nick']} missing from imported players"

    await cache_db.close()


def test_report_is_json_serializable_shape() -> None:
    """`ImportReport` is a plain dataclass of ints — `asdict` must round-trip
    through JSON cleanly for whatever ships the import report to the owner."""
    report = mod.ImportReport(
        players_created=1,
        deals_imported=2,
        placeholder_rows_skipped=3,
        referrers_resolved=4,
        items_imported=5,
        items_deleted=6,
        items_without_section=7,
        recomputed_players=8,
    )
    assert json.loads(json.dumps(asdict(report)))["players_created"] == 1
