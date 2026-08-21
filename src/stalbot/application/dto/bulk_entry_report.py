"""`BulkEntryReport` — result of parsing and applying a скупка calculator paste (Э8, §I.9)."""

from collections.abc import Sequence
from dataclasses import dataclass

from stalbot.domain.entities.catalog_item import CatalogItem


@dataclass(frozen=True, slots=True)
class BulkEntryReport:
    """What happened when a "Название xКоличество" block was applied to a draft.

    Every bucket holds the *raw* pasted line (not the parsed name) so the
    caller can show the owner exactly what they typed back for correction —
    the whole point of §I.9's "несопознанные строки возвращаются одним
    списком для правки, а не проваливают весь ввод".
    """

    applied: Sequence[tuple[CatalogItem, int]]
    """Lines that resolved to exactly one catalog item and were upserted."""

    removed: Sequence[CatalogItem]
    """Lines with `xКоличество` of `0` — resolved and deleted from the draft
    instead of upserted (the calculator's point-edit-by-repaste path)."""

    not_parsed: Sequence[str]
    """Lines that don't match "Название xКоличество", or whose quantity is out of range."""

    not_found: Sequence[str]
    """Lines that parsed fine but matched no catalog item by name."""

    ambiguous: Sequence[str]
    """Lines whose name matches a catalog item in *both* categories (§I.5) —
    resolving which one meant requires game knowledge, not automation."""
