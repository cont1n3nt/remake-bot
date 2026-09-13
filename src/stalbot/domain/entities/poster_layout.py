"""Stored poster layout: `poster_sections` / `poster_slots` rows (заявка 13.09.2026 п.6).

Deliberately named `...Row`, not `PosterSection`/`PosterSlot`: those names
already belong to `application/dto/poster_spec.py`, which describes a
poster *ready to draw* — resolved prices, absolute icon paths, packing
hints. These two are the stored layout the spec is built from. One is
editable data, the other a render instruction, and confusing the two is
exactly the bug this split prevents.
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.enums import PosterKind


@dataclass(frozen=True, slots=True)
class PosterSectionRow:
    """One block on a poster — a named group, or an unnamed column."""

    id: int | None
    """`None` for a not-yet-persisted section."""
    poster_kind: PosterKind
    name: str | None
    """`None` for the unnamed column-blocks of «Скуп ресурсов»/«Скуп ваших бустов»."""
    sort_order: int
    columns: int = 1
    """How many block-columns the section spans (e.g. boosts' «Медицина» is 2)."""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PosterSlotRow:
    """One item's place on a poster."""

    id: int | None
    section_id: int
    sort_order: int
    display_name: str
    name_norm: str
    """Fallback match into the catalog, used when `catalog_item_id` is NULL —
    the only way the JSON-era layout ever resolved a slot."""
    icon_file: str
    """Bare filename inside `Settings.poster_icons_dir`, never a path."""
    catalog_item_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
