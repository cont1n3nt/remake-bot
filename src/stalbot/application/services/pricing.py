"""Pricing: `/setprice`, `/setboost`, `/new_price`'s TXT round-trip (sqlite_migration.md Э7).

`catalog_items` is the only price surface left — the human-maintained price
sheets `/sync_prices` used to push onto are gone along with the rest of
Sheets (§VIII: `sync_prices`/`SyncPricesReport`/the command are deleted
outright, not ported, since there is nothing left to sync *to*). Every
price write also appends an `item_price_history` row (§IV.2).
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from stalbot.application.dto.price_change import PriceChange, group_price_changes
from stalbot.application.dto.price_import import PriceImportIssue, PriceImportPlan
from stalbot.application.ports.clock import Clock
from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.entities.item_price_history import ItemPriceHistoryEntry
from stalbot.domain.enums import ItemCategory, PriceChangeSource, PriceField
from stalbot.domain.errors import AmountParseError, ItemNotFoundError
from stalbot.domain.money import format_amount, parse_amount, to_storage
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.item_price_history import ItemPriceHistoryRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name

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
    """Reads/writes catalog item prices and logs every change."""

    def __init__(
        self,
        items: CatalogItemsRepository,
        history: ItemPriceHistoryRepository,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            items: Cache repository for the item catalog.
            history: Append-only log of every price change.
            clock: Time source, tz-aware `GMT3`, stamps `updated_at`.
        """
        self._items = items
        self._history = history
        self._clock = clock

    async def set_price(
        self, item_id: int, field: PriceField, amount: Decimal, *, changed_by: int | None = None
    ) -> PriceChange:
        """Set one item's buy or sell price directly (`/setprice`, `/setboost`).

        Args:
            item_id: Catalog id of the item to update.
            field: Which price to set.
            amount: The new price.
            changed_by: Discord id of the admin making the change, for the
                history log.

        Raises:
            ItemNotFoundError: No item with this id exists.
        """
        item = await self._items.get_by_id(item_id)
        if item is None:
            raise ItemNotFoundError(str(item_id))

        old_price = item.price_buy if field is PriceField.BUY else item.price_sell
        now = self._clock.now()
        stored_price = to_storage(amount)
        price_buy = stored_price if field is PriceField.BUY else item.price_buy
        price_sell = stored_price if field is PriceField.SELL else item.price_sell
        await self._items.set_price(item_id, price_buy=price_buy, price_sell=price_sell, now=now)
        await self._history.add(
            ItemPriceHistoryEntry(
                id=None,
                item_id=item_id,
                field=field,
                old_price=old_price,
                new_price=stored_price,
                changed_by=changed_by,
                source=PriceChangeSource.SETPRICE,
                changed_at=now,
            )
        )
        return PriceChange(
            item_id=item_id,
            item_name=item.name,
            category=item.category,
            field=field,
            old_price=old_price,
            new_price=stored_price,
        )

    async def preview_import(self, text: str) -> PriceImportPlan:
        """Parse and validate a `/give_price`-format TXT without writing anything.

        Args:
            text: The decoded TXT file content.

        Returns:
            Every valid change found, plus every rejected line. PLAN.md
            §10.6 step 4: if `issues` is non-empty, the caller must not apply
            `changes` — nothing here writes anything.
        """
        catalog = await self._items.all()
        by_id = {item.id: item for item in catalog}
        by_name_category = {(item.name_norm, item.category): item for item in catalog}

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
                new_buy = to_storage(parse_amount(buy_text)) if buy_text else None
                new_sell = to_storage(parse_amount(sell_text)) if sell_text else None
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
            assert item.id is not None  # noqa: S101 - a fetched item always has a persisted id

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

    async def apply_import(self, plan: PriceImportPlan, *, changed_by: int | None = None) -> None:
        """Write a validated `PriceImportPlan`'s changes.

        Args:
            plan: A plan with no `issues` (the caller must check `is_valid`
                itself — this never silently ignores a bad plan by being
                lenient, it simply trusts the precondition).
            changed_by: Discord id of the admin applying the import, for
                the history log.
        """
        by_item: dict[int, list[PriceChange]] = defaultdict(list)
        for change in plan.changes:
            by_item[change.item_id].append(change)
        if not by_item:
            return

        now = self._clock.now()
        # One batched lookup instead of one `get_by_id` per changed item (APP-6).
        items = await self._items.get_by_ids(list(by_item.keys()))
        for item_id, item_changes in by_item.items():
            item = items.get(item_id)
            if item is None:
                continue  # deleted since preview; nothing left to write
            price_buy, price_sell = item.price_buy, item.price_sell
            for change in item_changes:
                # `change.new_price` is already a rounded `Rub` — built that way by
                # `preview_import` — not a raw `Decimal` needing `to_storage()` again.
                stored_price = change.new_price
                if change.field is PriceField.BUY:
                    price_buy = stored_price
                else:
                    price_sell = stored_price
                await self._history.add(
                    ItemPriceHistoryEntry(
                        id=None,
                        item_id=item_id,
                        field=change.field,
                        old_price=change.old_price,
                        new_price=stored_price,
                        changed_by=changed_by,
                        source=PriceChangeSource.IMPORT,
                        changed_at=now,
                    )
                )
            await self._items.set_price(
                item_id, price_buy=price_buy, price_sell=price_sell, now=now
            )

    async def export_txt(self) -> str:
        """Render the full catalog as the fixed TXT format `/new_price` parses back."""
        items = await self._items.all()
        return render_price_list_txt(items, now=self._clock.now())


def render_price_list_txt(items: Sequence[CatalogItem], *, now: datetime) -> str:
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


def render_price_change_report(
    changes: Sequence[PriceChange], catalog: Sequence[CatalogItem]
) -> str:
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


def _price_or_dash(price: Decimal | int | None) -> str:
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


def _item_to_txt_cells(item: CatalogItem) -> tuple[str, str, str, str, str, str, str]:
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
