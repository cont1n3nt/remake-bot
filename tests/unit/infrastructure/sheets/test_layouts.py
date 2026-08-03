"""Sanity checks for `stalbot.infrastructure.sheets.layouts` (PLAN.md §6.1, §6.2)."""

from stalbot.domain.enums import ItemCategory, PriceField
from stalbot.infrastructure.sheets.layouts import (
    DATABASE_BLOCKS,
    EXPECTED_SHEET_TITLES,
    ITEMS_BLOCK,
    SYNC_LAYOUTS,
    TICKETS_BLOCK,
    USERS_BLOCK,
)


def test_database_blocks_cover_the_documented_column_ranges() -> None:
    spans = {(block.col_start, block.col_end) for block in DATABASE_BLOCKS}
    assert spans == {("A", "H"), ("I", "S"), ("U", "V"), ("AA", "AG")}


def test_tickets_block_header_matches_live_sheet() -> None:
    assert TICKETS_BLOCK.expected_headers == (
        "Дата",
        "Ник",
        "Покупка",
        "Продажа",
        "Сумма",
        "Coins",
        "XP",
        "Пришел от:",
    )


def test_users_block_header_has_eleven_columns() -> None:
    assert len(USERS_BLOCK.expected_headers) == 11


def test_items_block_header_matches_live_sheet() -> None:
    assert ITEMS_BLOCK.expected_headers == (
        "id",
        "item name",
        "category",
        "price_buy",
        "price_sell",
        "emoji",
        "updated_at",
    )


def test_expected_sheet_titles_match_the_real_spreadsheet() -> None:
    assert EXPECTED_SHEET_TITLES == frozenset({"DataBase", "Мейн скуп", "Скуп бустов", "БУСТЫ"})


def test_sync_layouts_have_matching_name_and_price_column_counts() -> None:
    for layout in SYNC_LAYOUTS:
        assert len(layout.name_columns) == len(layout.price_columns)


def test_boosts_sheet_feeds_price_sell_and_boost_category() -> None:
    boosts = next(layout for layout in SYNC_LAYOUTS if layout.sheet == "БУСТЫ")
    assert boosts.category is ItemCategory.BOOST
    assert boosts.price_field is PriceField.SELL


def test_resource_sheets_feed_price_buy() -> None:
    resource_layouts = [
        layout for layout in SYNC_LAYOUTS if layout.category is ItemCategory.RESOURCE
    ]
    assert resource_layouts
    assert all(layout.price_field is PriceField.BUY for layout in resource_layouts)
