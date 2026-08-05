"""Pricing: `/setprice`, `/setboost`, `/sync_prices`, `/new_price`'s TXT round-trip.

Two independent price surfaces meet here (PLAN.md §6.2, §10.5-§10.8):

- the `item database` block (`AA:AG`), which the bot owns, every ticket
  calculation reads from, and which is the source of truth for prices;
- the human-maintained price sheets (`Мейн скуп`, `Скуп бустов`, `БУСТЫ`),
  which display those same prices for staff to read at a glance.

`/setprice`/`/setboost` and `/new_price` write the item database directly;
`/sync_prices` (UX #7) pushes the item database's current prices out onto
the price sheets, keyed by matching each sheet row's item name to a catalog
entry — it is the one place that writes to the price sheets. This is the
opposite of `/sync_prices`'s original direction (reading the price sheets
into the item database); the price sheets are no longer an independently
editable source of truth, only a display surface the bot keeps in sync.
"""

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation

from stalbot.application.dto.price_change import PriceChange, group_price_changes
from stalbot.application.dto.price_import import PriceImportIssue, PriceImportPlan
from stalbot.application.dto.sync_prices_report import SyncPricesReport
from stalbot.application.ports.clock import Clock
from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory, PriceField
from stalbot.domain.errors import AmountParseError, ItemNotFoundError
from stalbot.domain.money import format_amount, parse_amount, round_for_storage
from stalbot.infrastructure.cache.repositories.items import (
    ItemsCacheRepository,
    normalize_item_name,
)
from stalbot.infrastructure.sheets.a1 import a1_range
from stalbot.infrastructure.sheets.client import CellGrid, SheetsClient
from stalbot.infrastructure.sheets.layouts import DATABASE_SHEET, SYNC_LAYOUTS, SheetLayout

logger = logging.getLogger(__name__)

_PRICE_COLUMN: dict[PriceField, str] = {PriceField.BUY: "AD", PriceField.SELL: "AE"}
_UPDATED_COLUMN = "AG"

_TXT_COLUMNS: tuple[str, ...] = (
    "ID",
    "Название",
    "Категория",
    "Скуп",
    "Продажа",
    "Эмодзи",
    "Обновлено",
)
_TXT_FIELD_COUNT = len(_TXT_COLUMNS)
_SEPARATOR_CHARS = frozenset("-+ \t")


class PricingService:
    """Reads/writes the item database's prices and syncs them from the price sheets."""

    def __init__(self, sheets: SheetsClient, items: ItemsCacheRepository, *, clock: Clock) -> None:
        """Wire the service to its collaborators.

        Args:
            sheets: Sheets access (writes are protected + RAW, PLAN.md §7.3).
            items: Cache repository for the item database.
            clock: Time source, tz-aware `GMT3`, stamps `updated_at`.
        """
        self._sheets = sheets
        self._items = items
        self._clock = clock

    async def set_price(self, item_id: int, field: PriceField, amount: Decimal) -> PriceChange:
        """Set one item's buy or sell price directly (`/setprice`, `/setboost`).

        Args:
            item_id: Catalog id of the item to update.
            field: Which price to set.
            amount: The new price.

        Raises:
            ItemNotFoundError: No item with this id exists.
        """
        item = await self._items.get_by_id(item_id)
        if item is None:
            raise ItemNotFoundError(str(item_id))

        old_price = item.price_buy if field is PriceField.BUY else item.price_sell
        now = self._clock.now()
        stored_price = round_for_storage(amount)
        await self._sheets.write_verified(
            {
                a1_range(DATABASE_SHEET, _PRICE_COLUMN[field], item.row): [[int(stored_price)]],
                a1_range(DATABASE_SHEET, _UPDATED_COLUMN, item.row): [[format_datetime(now)]],
            }
        )
        updated = _apply_price(item, field, stored_price, updated_at=now)
        await self._items.upsert_many([updated])
        return PriceChange(
            item_id=item.id,
            item_name=item.name,
            category=item.category,
            field=field,
            old_price=old_price,
            new_price=stored_price,
        )

    async def sync_prices(self) -> SyncPricesReport:
        """Push the item database's current prices onto every price sheet (UX #7, PLAN.md §10.8).

        The item database is the source of truth: for each row on a
        `SYNC_LAYOUTS` sheet whose name cell matches a catalog item, that
        item's current price (for the sheet's `price_field`) is written into
        the row's price cell — replacing whatever was there, not the other
        way around. A name with no catalog match is reported via
        `not_found` and left untouched (nothing to source a price from).
        """
        name_ranges = [
            _column_range(layout, col) for layout in SYNC_LAYOUTS for col in layout.name_columns
        ]
        price_ranges = [
            _column_range(layout, col) for layout in SYNC_LAYOUTS for col in layout.price_columns
        ]
        result = await self._sheets.batch_get([*name_ranges, *price_ranges])

        catalog = await self._items.all()
        by_key = {(normalize_item_name(item.name), item.category): item for item in catalog}
        changes: list[PriceChange] = []
        not_found: list[str] = []
        overwritten_garbage: list[str] = []
        unchanged = 0
        data: dict[str, CellGrid] = {}

        for layout in SYNC_LAYOUTS:
            for name_col, price_col in zip(layout.name_columns, layout.price_columns, strict=True):
                names = result.get(_column_range(layout, name_col), [])
                prices = result.get(_column_range(layout, price_col), [])
                for offset, row in enumerate(layout.rows):
                    name_text = _cell_text(names, offset)
                    if not name_text:
                        continue
                    item = by_key.get((normalize_item_name(name_text), layout.category))
                    if item is None:
                        not_found.append(name_text)
                        continue

                    new_price = _current_price(item, layout.price_field)
                    had_garbage = False
                    try:
                        old_price = _cell_decimal(prices, offset)
                    except InvalidOperation:
                        # The sheet is the destination now, not the source —
                        # unlike APP-3's original concern (misreading garbage
                        # as "price cleared"), garbage here is simply
                        # overwritten with the catalog's real price, same as
                        # any other stale cell. Reported separately so an
                        # admin can see which cells had bad content. Always
                        # treated as a change (even if `new_price` also
                        # happens to be `None`) — the cell isn't actually
                        # empty, so leaving it alone here would silently
                        # contradict the "overwritten" report below.
                        old_price = None
                        had_garbage = True
                        overwritten_garbage.append(name_text)
                    if not had_garbage and new_price == old_price:
                        unchanged += 1
                        continue

                    changes.append(_price_change(item, layout.price_field, old_price, new_price))
                    data[a1_range(layout.sheet, price_col, row)] = [
                        [int(new_price) if new_price is not None else ""]
                    ]

        if data:
            await self._sheets.batch_update(data)

        return SyncPricesReport(
            updated=tuple(changes),
            not_found=tuple(not_found),
            unchanged_count=unchanged,
            unparseable=tuple(overwritten_garbage),
        )

    async def preview_import(self, text: str) -> PriceImportPlan:
        """Parse and validate a `/give_price`-format TXT without writing anything.

        Args:
            text: The decoded TXT file content.

        Returns:
            Every valid change found, plus every rejected line. PLAN.md
            §10.6 step 4: if `issues` is non-empty, the caller must not apply
            `changes` — nothing here writes to Sheets.
        """
        catalog = await self._items.all()
        by_id = {item.id: item for item in catalog}
        by_name_category = {
            (normalize_item_name(item.name), item.category): item for item in catalog
        }

        changes: list[PriceChange] = []
        issues: list[PriceImportIssue] = []
        seen_ids: set[int] = set()

        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#") or _is_separator_line(line):
                continue
            cells = [cell.strip() for cell in line.split("|")]
            if tuple(cells) == _TXT_COLUMNS:
                continue
            if len(cells) != _TXT_FIELD_COUNT:
                issues.append(
                    PriceImportIssue(
                        line_number, f"ожидается {_TXT_FIELD_COUNT} полей, найдено {len(cells)}"
                    )
                )
                continue

            id_text, name, category_text, buy_text, sell_text, _emoji, _updated = cells
            try:
                item_id = int(id_text)
            except ValueError:
                issues.append(PriceImportIssue(line_number, f"некорректный ID: {id_text!r}"))
                continue
            if item_id in seen_ids:
                issues.append(PriceImportIssue(line_number, f"повторный ID: {item_id}"))
                continue
            seen_ids.add(item_id)

            category: ItemCategory | None = None
            if category_text:
                try:
                    category = ItemCategory(category_text)
                except ValueError:
                    issues.append(
                        PriceImportIssue(line_number, f"некорректная категория: {category_text!r}")
                    )
                    continue

            try:
                new_buy = round_for_storage(parse_amount(buy_text)) if buy_text else None
                new_sell = round_for_storage(parse_amount(sell_text)) if sell_text else None
            except AmountParseError as exc:
                issues.append(PriceImportIssue(line_number, f"некорректная цена: {exc}"))
                continue
            if (new_buy is not None and new_buy < 0) or (new_sell is not None and new_sell < 0):
                issues.append(PriceImportIssue(line_number, "цена не может быть отрицательной"))
                continue

            item = by_id.get(item_id)
            if item is None and category is not None:
                item = by_name_category.get((normalize_item_name(name), category))
            if item is None:
                issues.append(PriceImportIssue(line_number, f"ID {item_id} не найден в базе"))
                continue

            if new_buy != item.price_buy:
                changes.append(
                    PriceChange(
                        item_id=item.id,
                        item_name=item.name,
                        category=item.category,
                        field=PriceField.BUY,
                        old_price=item.price_buy,
                        new_price=new_buy,
                    )
                )
            if new_sell != item.price_sell:
                changes.append(
                    PriceChange(
                        item_id=item.id,
                        item_name=item.name,
                        category=item.category,
                        field=PriceField.SELL,
                        old_price=item.price_sell,
                        new_price=new_sell,
                    )
                )

        return PriceImportPlan(changes=tuple(changes), issues=tuple(issues))

    async def apply_import(self, plan: PriceImportPlan) -> None:
        """Write a validated `PriceImportPlan`'s changes in one batch.

        Args:
            plan: A plan with no `issues` (the caller must check `is_valid`
                itself — this never silently ignores a bad plan by being
                lenient, it simply trusts the precondition).
        """
        by_item: dict[int, list[PriceChange]] = defaultdict(list)
        for change in plan.changes:
            by_item[change.item_id].append(change)

        now = self._clock.now()
        data: dict[str, CellGrid] = {}
        updated_items: list[Item] = []
        # One batched lookup instead of one `get_by_id` per changed item (APP-6).
        items = await self._items.get_by_ids(list(by_item.keys()))
        for item_id, item_changes in by_item.items():
            item = items.get(item_id)
            if item is None:
                continue  # renumbered/deleted since preview; nothing left to write
            for change in item_changes:
                stored_price = (
                    round_for_storage(change.new_price) if change.new_price is not None else None
                )
                item = _apply_price(item, change.field, stored_price, updated_at=now)
                data[a1_range(DATABASE_SHEET, _PRICE_COLUMN[change.field], item.row)] = [
                    [int(stored_price) if stored_price is not None else ""]
                ]
            data[a1_range(DATABASE_SHEET, _UPDATED_COLUMN, item.row)] = [[format_datetime(now)]]
            updated_items.append(item)

        if not data:
            return
        await self._sheets.batch_update(data)
        await self._items.upsert_many(updated_items)

    async def export_txt(self) -> str:
        """Render the full catalog as the fixed TXT format `/new_price` parses back."""
        items = await self._items.all()
        return render_price_list_txt(items, now=self._clock.now())


def render_price_list_txt(items: Sequence[Item], *, now: datetime) -> str:
    """Pure formatter for `/give_price`'s TXT export (PLAN.md §10.5).

    Args:
        items: The catalog to export, in the order to list them.
        now: Timestamp for the header's "выгружено" line.
    """
    comment_lines = (
        f"# Прайс-лист Stalzone — выгружено {format_datetime(now)} (GMT+3)",
        '# Меняйте ТОЛЬКО колонки "Скуп" и "Продажа". Строки со знаком # игнорируются.',
        "# Формат числа: 250000, 250 000 или 250к — всё будет понято корректно.",
        "# Пустое значение = цены нет.",
        "#",
    )
    rows = [_item_to_txt_cells(item) for item in items]
    widths = [
        max(len(_TXT_COLUMNS[i]), *(len(row[i]) for row in rows)) if rows else len(_TXT_COLUMNS[i])
        for i in range(_TXT_FIELD_COUNT)
    ]
    lines = [
        *comment_lines,
        _pad_row(_TXT_COLUMNS, widths),
        "-+-".join("-" * width for width in widths),
        *(_pad_row(row, widths) for row in rows),
    ]
    return "\n".join(lines) + "\n"


def render_price_change_report(changes: Sequence[PriceChange], catalog: Sequence[Item]) -> str:
    """Render the one price-change report format every pricing command shares (§10.6 step 8).

    Shared by `/setprice`, `/setboost` and `/new_price` (PLAN.md §10.7:
    "тот же рендерер отчёта, что у `/new_price`") so a single-item change and
    a bulk import look identical to the admin reading them.

    Args:
        changes: Every price change to report.
        catalog: The full catalog, used by `group_price_changes` to detect
            the "скуп бустов" resource/boost name collision.
    """
    grouped = group_price_changes(changes, catalog)
    blocks = [
        _render_group(title, group)
        for title, group in (
            ("🪙 Изменение цен на ресурсы:", grouped.resources),
            ("🚀 Изменение цен на бусты:", grouped.boosts),
            ("🪙 Изменение цен на скуп бустов:", grouped.boost_scalp),
        )
        if group
    ]
    return "\n\n".join(blocks) if blocks else "Изменений нет."


def _render_group(title: str, changes: Sequence[PriceChange]) -> str:
    lines = [title]
    lines.extend(
        f" • {change.item_name} | {_price_or_dash(change.old_price)} → "
        f"{_price_or_dash(change.new_price)}"
        for change in changes
    )
    return "\n".join(lines)


def _price_or_dash(price: Decimal | None) -> str:
    return format_amount(price) if price is not None else "—"


def decode_price_list_bytes(data: bytes) -> str:
    """Decode a `/new_price` TXT attachment, auto-detecting UTF-8 (with BOM) vs CP1251.

    Args:
        data: Raw attachment bytes.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def _pad_row(cells: Sequence[str], widths: Sequence[int]) -> str:
    return " | ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))


def _item_to_txt_cells(item: Item) -> tuple[str, str, str, str, str, str, str]:
    return (
        str(item.id),
        item.name,
        item.category.value,
        format_amount(item.price_buy, currency=False) if item.price_buy is not None else "",
        format_amount(item.price_sell, currency=False) if item.price_sell is not None else "",
        item.emoji or "",
        format_datetime(item.updated_at) if item.updated_at is not None else "",
    )


def _is_separator_line(line: str) -> bool:
    return bool(line) and all(ch in _SEPARATOR_CHARS for ch in line)


def _apply_price(
    item: Item, field: PriceField, amount: Decimal | None, *, updated_at: datetime
) -> Item:
    if field is PriceField.BUY:
        return replace(item, price_buy=amount, updated_at=updated_at)
    return replace(item, price_sell=amount, updated_at=updated_at)


def _current_price(item: Item, field: PriceField) -> Decimal | None:
    return item.price_buy if field is PriceField.BUY else item.price_sell


def _price_change(
    item: Item, field: PriceField, old_price: Decimal | None, new_price: Decimal | None
) -> PriceChange:
    return PriceChange(
        item_id=item.id,
        item_name=item.name,
        category=item.category,
        field=field,
        old_price=old_price,
        new_price=new_price,
    )


def _column_range(layout: SheetLayout, column: str) -> str:
    return a1_range(layout.sheet, column, layout.rows.start, column, layout.rows.stop - 1)


def _cell_text(rows: CellGrid, offset: int) -> str:
    if offset >= len(rows) or not rows[offset]:
        return ""
    return str(rows[offset][0]).strip()


def _cell_decimal(rows: CellGrid, offset: int) -> Decimal | None:
    """Parse a price cell, or `None` if the cell itself is genuinely empty.

    Raises:
        InvalidOperation: If the cell has non-empty content that isn't a
            valid number — the caller must not treat this the same as an
            empty cell (APP-3), or it reads as "price cleared" and silently
            wipes an existing price.
    """
    if offset >= len(rows) or not rows[offset]:
        return None
    value = rows[offset][0]
    if value is None or value == "":
        return None
    return Decimal(str(value))
