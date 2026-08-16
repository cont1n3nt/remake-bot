"""`PosterSpec` — everything `PosterRenderer` needs to draw one poster (Часть IX, Э11).

Built by `PosterService.build()` from the frozen layout JSON (item order/
icons, extracted once from `СКУПКА.xlsx`) plus live prices from
`catalog_items`. Carries no `Rub`/`Kopeks` arithmetic of its own — prices
here are already resolved, display-ready strings.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PosterSlot:
    """One item's icon, name, and already-formatted price."""

    name: str
    price_text: str
    icon_path: Path


@dataclass(frozen=True, slots=True)
class PosterSection:
    """A named group of slots (only «Скрин бусты» has real sections), or one unnamed group."""

    name: str | None
    slots: tuple[PosterSlot, ...]


@dataclass(frozen=True, slots=True)
class PosterSpec:
    """One poster's title, logo, and sections, ready to render."""

    title: str
    logo_path: Path
    sections: tuple[PosterSection, ...]
