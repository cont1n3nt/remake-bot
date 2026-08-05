"""Declarative map of the spreadsheet's layout (see PLAN.md §6.1, §6.2).

Two kinds of structure live here:

- `DATABASE_BLOCKS` — the four data blocks on the `DataBase` sheet, with the
  exact header text `validate_layout` checks at startup. Captured directly
  from the live spreadsheet (`SPREADSHEET_ID` in `.env.example`), not
  transcribed from the plan's prose, because header wording, capitalization
  and even the sheet's own display name turned out to differ in small ways
  (e.g. the price sheet is titled "Мейн скуп", lower-case `с`, not "Мейн
  Скуп" — PLAN.md §17.5 flags exactly this risk).
- `SYNC_LAYOUTS` — the price sheets `/sync_prices` reads from (M6). Adding a
  sheet is one tuple entry, no logic changes (PLAN.md §6.2).
"""

from dataclasses import dataclass
from typing import Final

from stalbot.domain.enums import ItemCategory, PriceField

DATABASE_SHEET: Final = "DataBase"

#: First row of the Тикеты/Юзеры/Магазин/item database data blocks; row 1 is
#: a merged section title, row 2 the header row confirmed live.
DATA_START_ROW: Final = 3
HEADER_ROW: Final = 2


@dataclass(frozen=True, slots=True)
class DatabaseBlock:
    """One of the four labeled column blocks on the `DataBase` sheet."""

    name: str
    col_start: str
    col_end: str
    expected_headers: tuple[str, ...]


TICKETS_BLOCK: Final = DatabaseBlock(
    name="Тикеты",
    col_start="A",
    col_end="H",
    expected_headers=(
        "Дата",
        "Ник",
        "Покупка",
        "Продажа",
        "Сумма",
        "Coins",
        "XP",
        "Пришел от:",
    ),
)

USERS_BLOCK: Final = DatabaseBlock(
    name="Общая база пользователей",
    col_start="I",
    col_end="S",
    expected_headers=(
        "Discord ID",
        "Уникальный ник",
        "Всего Coins",
        "Всего XP",
        "Оборот покупок",
        "Оборот Продаж",
        "Общий оборот",
        "Рефералы",
        "Бустер сервера",
        "Ранг",
        "Роль реферала",
    ),
)

SHOP_BLOCK: Final = DatabaseBlock(
    name="Магазин",
    col_start="U",
    col_end="V",
    expected_headers=("Ник", "Трата коинов"),
)

ITEMS_BLOCK: Final = DatabaseBlock(
    name="item database",
    col_start="AA",
    col_end="AG",
    expected_headers=(
        "id",
        "item name",
        "category",
        "price_buy",
        "price_sell",
        "emoji",
        "updated_at",
    ),
)

#: Every block `validate_layout` checks at startup and on each full sync.
DATABASE_BLOCKS: Final[tuple[DatabaseBlock, ...]] = (
    TICKETS_BLOCK,
    USERS_BLOCK,
    SHOP_BLOCK,
    ITEMS_BLOCK,
)


@dataclass(frozen=True, slots=True)
class SheetLayout:
    """One price sheet's repeating name/price column pairs (PLAN.md §6.2)."""

    sheet: str
    rows: range
    name_columns: tuple[str, ...]
    price_columns: tuple[str, ...]
    category: ItemCategory
    price_field: PriceField


#: Declarative price-sheet map `/sync_prices` writes to (UX #7 — item database
#: is the source of truth, these sheets are the destination). Adding a sheet
#: is one entry here, no code changes elsewhere. Start rows confirmed live
#: (each sheet's title/header rows occupy everything above): "Мейн скуп" from
#: row 5, "БУСТЫ" from row 4, "Скуп бустов" from row 3 — end rows unchanged
#: from before this fix, to avoid reading/writing past real data.
SYNC_LAYOUTS: Final[tuple[SheetLayout, ...]] = (
    SheetLayout(
        sheet="Мейн скуп",
        rows=range(5, 32),
        name_columns=("C", "J", "Q", "X", "AE", "AL", "AS"),
        price_columns=("D", "K", "R", "Y", "AF", "AM", "AT"),
        category=ItemCategory.RESOURCE,
        price_field=PriceField.BUY,
    ),
    SheetLayout(
        sheet="Скуп бустов",
        rows=range(3, 10),
        name_columns=("C", "J", "Q", "X"),
        price_columns=("D", "K", "R", "Y"),
        category=ItemCategory.RESOURCE,
        price_field=PriceField.BUY,
    ),
    SheetLayout(
        sheet="БУСТЫ",
        rows=range(4, 10),
        name_columns=("C", "J", "Q", "X", "AE", "AL", "AS"),
        price_columns=("D", "K", "R", "Y", "AF", "AM", "AT"),
        category=ItemCategory.BOOST,
        price_field=PriceField.SELL,
    ),
)

#: Every worksheet title the bot expects to find, confirmed live. Startup
#: fails fast with a clear message if any is missing or misspelled.
EXPECTED_SHEET_TITLES: Final[frozenset[str]] = frozenset(
    {DATABASE_SHEET, "Мейн скуп", "Скуп бустов", "БУСТЫ"}
)
