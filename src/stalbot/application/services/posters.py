"""`PosterService.build` — layout JSON + live prices -> `PosterSpec` (Часть IX, Э11).

Deliberately does not import PIL — `tests/unit/test_architecture_invariants.py`
holds every layer but `infrastructure/posters/` to that.
"""

import json
from importlib import resources
from pathlib import Path
from typing import Any, Final

from stalbot.application.dto.poster_spec import PosterSection, PosterSlot, PosterSpec
from stalbot.domain.enums import ItemCategory, PosterKind
from stalbot.domain.money import format_amount
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository

_TITLE_BY_KIND: Final[dict[PosterKind, str]] = {
    PosterKind.RESOURCES: "Скуп ресурсов",
    PosterKind.BOOSTS: "Продажа бустов",
    PosterKind.BOOST_PURCHASES: "Скуп ваших бустов",
}

#: (`/себестоимость`-style "which side of the trade" mapping, §I.5) —
#: RESOURCES/BOOST_PURCHASES both price against the bot's *buy* side even
#: though BOOST_PURCHASES only lists a curated subset of it.
_CATEGORY_BY_KIND: Final[dict[PosterKind, ItemCategory]] = {
    PosterKind.RESOURCES: ItemCategory.RESOURCE,
    PosterKind.BOOSTS: ItemCategory.BOOST,
    PosterKind.BOOST_PURCHASES: ItemCategory.RESOURCE,
}

#: How many section-blocks `PillowRenderer` places side by side per row.
#: Boosts: 4, matching the reference sheet's 4-columns-by-2-rows layout
#: (owner bug report, Э11 — «Медицина» is one section with `"columns": 2`
#: in the layout JSON, filling out the row's other two column-widths).
#: Boost_purchases: 3. Resources: 10 —
#: its 160 items have no header row in the source sheet at all, but
#: `extract_poster_assets.py` still sorts anchors by `(col, row)`, so
#: `layout_resources.json`'s unnamed sections are the sheet's *real*
#: columns, not an invented split.
_BLOCKS_PER_ROW_BY_KIND: Final[dict[PosterKind, int]] = {
    PosterKind.RESOURCES: 10,
    PosterKind.BOOSTS: 4,
    PosterKind.BOOST_PURCHASES: 3,
}

#: How wide (in block-columns) the logo's reserved cell is. Boosts/
#: boost_purchases: 1 — both reference sheets place it as a single
#: normal-width column between two of their blocks. Resources: 2, a
#: "normal" visible size inline among its narrower single-column blocks.
_LOGO_WIDTH_BY_KIND: Final[dict[PosterKind, int]] = {
    PosterKind.RESOURCES: 2,
    PosterKind.BOOSTS: 1,
    PosterKind.BOOST_PURCHASES: 1,
}

#: Kinds whose logo goes right before the *last* section (owner bug
#: report, Э11 — boosts: between «Боеприпасы» and «Прочее»; boost_purchases:
#: between «Самогоноварение» and «Пиротехника», both matching the reference
#: sheets' fixed section order) rather than the generic midpoint default.
_LOGO_BEFORE_LAST_KINDS: Final = (PosterKind.BOOSTS, PosterKind.BOOST_PURCHASES)


def _logo_position(kind: PosterKind, section_count: int) -> int:
    """Where the logo is inserted into the section sequence before packing.

    Computed from `section_count` rather than a fixed index since a section
    can vanish entirely if every item in it lost its catalog price
    (empty-slot rule).
    """
    if kind in _LOGO_BEFORE_LAST_KINDS:
        return max(section_count - 1, 0)
    return section_count // 2


class PosterService:
    """Builds a `PosterSpec` for `PosterRenderer`, from frozen layout + live catalog prices."""

    def __init__(self, catalog_items: CatalogItemsRepository) -> None:
        """Wire the service to its collaborator.

        Args:
            catalog_items: Source of live prices for every slot.
        """
        self._catalog_items = catalog_items

    async def build(self, kind: PosterKind) -> PosterSpec:
        """Build the spec for *kind*, skipping any item with no price (empty-slot rule, Часть IX).

        Args:
            kind: Which of the three posters to build.
        """
        layout = _load_layout(kind)
        category = _CATEGORY_BY_KIND[kind]
        catalog = await self._catalog_items.all()
        by_name_norm = {
            item.name_norm: item
            for item in catalog
            if item.category is category and item.deleted_at is None
        }

        sections: list[PosterSection] = []
        for section in layout["sections"]:
            slots: list[PosterSlot] = []
            for entry in section["items"]:
                item = by_name_norm.get(entry["name_norm"])
                if item is None:
                    continue
                price = item.price_sell if category is ItemCategory.BOOST else item.price_buy
                if price is None:
                    continue
                slots.append(
                    PosterSlot(
                        name=entry["name"],
                        # "р.", not `format_amount`'s default "₽" — Comic
                        # Relief (the poster font, chosen for Cyrillic
                        # support) has no glyph for U+20BD. Same reason the
                        # thousands-separator narrow no-break space (U+202F)
                        # from `format_amount` is swapped for a plain space
                        # here: the font has no glyph for it either, so it
                        # rendered as a tofu square between digit groups.
                        price_text=(
                            f"{format_amount(price, currency=False).replace(chr(0x202F), ' ')} р."
                        ),
                        icon_path=_assets_dir() / entry["icon"],
                    )
                )
            if slots:
                sections.append(
                    PosterSection(
                        name=section["name"],
                        slots=tuple(slots),
                        columns=section.get("columns", 1),
                    )
                )

        return PosterSpec(
            title=_TITLE_BY_KIND[kind],
            logo_path=_assets_dir() / "logo.png",
            sections=tuple(sections),
            blocks_per_row=_BLOCKS_PER_ROW_BY_KIND[kind],
            logo_position=_logo_position(kind, len(sections)),
            logo_width=_LOGO_WIDTH_BY_KIND[kind],
        )


def _assets_dir() -> Path:
    return Path(str(resources.files("stalbot") / "assets" / "posters"))


def _load_layout(kind: PosterKind) -> dict[str, list[dict[str, Any]]]:
    text = (_assets_dir() / f"layout_{kind.value}.json").read_text(encoding="utf-8")
    result: dict[str, list[dict[str, Any]]] = json.loads(text)
    return result
