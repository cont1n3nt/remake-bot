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
    columns: int = 1
    """How many block-columns this section spans (layout JSON's per-section
    `"columns"`, default 1). E.g. boosts' «Медицина» is 2 — one header bar
    over two card columns, filled column-major (owner bug report, Э11),
    rather than two separate single-column blocks."""


@dataclass(frozen=True, slots=True)
class PosterSpec:
    """One poster's title, logo, and sections, ready to render."""

    title: str
    logo_path: Path
    sections: tuple[PosterSection, ...]
    blocks_per_row: int
    """How many section-blocks sit side by side per row before wrapping —
    kind-specific (boosts: 4, matching the reference sheet's 4-column
    layout; boost_purchases: 3; resources: 10, matching its real column
    count), set by `PosterService`."""
    logo_position: int
    """Index into `sections` the logo is inserted before when packing rows
    (kind-specific — e.g. boosts places it right before the last section,
    "Прочее"; resources/boost_purchases default to the midpoint), set by
    `PosterService`."""
    logo_width: int
    """How many block-columns wide the logo's reserved cell is (kind-specific
    — boosts: 1, matching its single-column-per-block reference layout;
    resources/boost_purchases: 2, a "normal" visible size), set by
    `PosterService`."""
