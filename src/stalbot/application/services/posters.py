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
                        # support) has no glyph for U+20BD.
                        price_text=f"{format_amount(price, currency=False)} р.",
                        icon_path=_assets_dir() / entry["icon"],
                    )
                )
            if slots:
                sections.append(PosterSection(name=section["name"], slots=tuple(slots)))

        return PosterSpec(
            title=_TITLE_BY_KIND[kind],
            logo_path=_assets_dir() / "logo.png",
            sections=tuple(sections),
        )


def _assets_dir() -> Path:
    return Path(str(resources.files("stalbot") / "assets" / "posters"))


def _load_layout(kind: PosterKind) -> dict[str, list[dict[str, Any]]]:
    text = (_assets_dir() / f"layout_{kind.value}.json").read_text(encoding="utf-8")
    result: dict[str, list[dict[str, Any]]] = json.loads(text)
    return result
