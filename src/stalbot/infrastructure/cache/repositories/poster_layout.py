"""SQLite-backed poster layout (заявка 13.09.2026 п.6, migration 0011).

One repository for both tables: `poster_slots` is meaningless without the
section it sits in, every read wants them joined, and nothing else reads
either table on its own — the same reasoning `ShelterRepository` already
follows for its five.
"""

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.poster_layout import PosterSectionRow, PosterSlotRow
from stalbot.domain.enums import PosterKind
from stalbot.infrastructure.cache.db import transaction


class PosterLayoutRepository:
    """CRUD over the stored poster layout."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    # --- reads -------------------------------------------------------------

    async def sections(self, kind: PosterKind) -> Sequence[PosterSectionRow]:
        """Return every section of one poster, in display order.

        Args:
            kind: Which poster to read.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM poster_sections WHERE poster_kind = ? ORDER BY sort_order, id",
            (kind.value,),
        )
        return [_row_to_section(row) async for row in cursor]

    async def slots(self, section_id: int) -> Sequence[PosterSlotRow]:
        """Return every slot in a section, in display order.

        Args:
            section_id: The section to read.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM poster_slots WHERE section_id = ? ORDER BY sort_order, id",
            (section_id,),
        )
        return [_row_to_slot(row) async for row in cursor]

    async def layout(
        self, kind: PosterKind
    ) -> list[tuple[PosterSectionRow, Sequence[PosterSlotRow]]]:
        """Return one poster's whole layout, sections and their slots, in order.

        Two queries rather than one per section (APP-6): the join is done
        in Python because a section with no slots must still come back, and
        a `LEFT JOIN` would need unpacking either way.

        Args:
            kind: Which poster to read.
        """
        sections = await self.sections(kind)
        if not sections:
            return []
        placeholders = ",".join("?" * len(sections))
        cursor = await self._conn.execute(
            f"SELECT * FROM poster_slots WHERE section_id IN ({placeholders}) "  # noqa: S608 - only `?` characters are interpolated
            "ORDER BY section_id, sort_order, id",
            [section.id for section in sections],
        )
        by_section: dict[int, list[PosterSlotRow]] = {}
        async for row in cursor:
            by_section.setdefault(row["section_id"], []).append(_row_to_slot(row))
        return [(section, by_section.get(section.id or 0, [])) for section in sections]

    async def find_slot(self, kind: PosterKind, catalog_item_id: int) -> PosterSlotRow | None:
        """Return the slot an item already occupies on *kind*, if any.

        Args:
            kind: Which poster to look on.
            catalog_item_id: The catalog item to look for.
        """
        cursor = await self._conn.execute(
            "SELECT s.* FROM poster_slots s "
            "JOIN poster_sections sec ON sec.id = s.section_id "
            "WHERE sec.poster_kind = ? AND s.catalog_item_id = ? "
            "ORDER BY s.sort_order, s.id LIMIT 1",
            (kind.value, catalog_item_id),
        )
        row = await cursor.fetchone()
        return _row_to_slot(row) if row is not None else None

    async def slots_for_item(self, catalog_item_id: int) -> Sequence[PosterSlotRow]:
        """Return every slot bound to a catalog item, across all posters.

        Args:
            catalog_item_id: The catalog item to look for.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM poster_slots WHERE catalog_item_id = ? ORDER BY id", (catalog_item_id,)
        )
        return [_row_to_slot(row) async for row in cursor]

    async def icon_files(self) -> set[str]:
        """Every icon filename any slot references — for orphan cleanup."""
        cursor = await self._conn.execute("SELECT DISTINCT icon_file FROM poster_slots")
        return {row["icon_file"] async for row in cursor}

    # --- writes ------------------------------------------------------------

    async def add_section(self, section: PosterSectionRow, *, now: datetime) -> int:
        """Insert a section, returning its assigned id.

        Args:
            section: The section to persist. `section.id` is ignored.
            now: Timestamp for `created_at`.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO poster_sections
                    (poster_kind, name, sort_order, columns, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    section.poster_kind.value,
                    section.name,
                    section.sort_order,
                    section.columns,
                    now.isoformat(),
                ),
            )
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is set right after a successful INSERT
        return new_id

    async def get_or_create_section(
        self, kind: PosterKind, name: str | None, *, now: datetime
    ) -> PosterSectionRow:
        """Find a named section on *kind*, or append a new one at the end.

        Only matches by name, so the unnamed column-blocks (`name is None`)
        are never reused — a new slot can't silently land in one of «Скуп
        ресурсов»' 28 anonymous columns just because it was asked for
        without a section.

        Args:
            kind: Which poster the section is on.
            name: Section name. `None` always creates a new unnamed section.
            now: Timestamp for `created_at`.
        """
        if name is not None:
            cursor = await self._conn.execute(
                "SELECT * FROM poster_sections WHERE poster_kind = ? AND name = ? "
                "ORDER BY sort_order, id LIMIT 1",
                (kind.value, name),
            )
            row = await cursor.fetchone()
            if row is not None:
                return _row_to_section(row)

        existing = await self.sections(kind)
        section = PosterSectionRow(
            id=None,
            poster_kind=kind,
            name=name,
            sort_order=(max((s.sort_order for s in existing), default=-1) + 1),
        )
        section_id = await self.add_section(section, now=now)
        return PosterSectionRow(
            id=section_id,
            poster_kind=kind,
            name=name,
            sort_order=section.sort_order,
            columns=section.columns,
            created_at=now,
        )

    async def add_slot(self, slot: PosterSlotRow, *, now: datetime) -> int:
        """Insert a slot, returning its assigned id.

        Args:
            slot: The slot to persist. `slot.id` is ignored.
            now: Timestamp for `created_at`.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO poster_slots
                    (section_id, sort_order, catalog_item_id, display_name,
                     name_norm, icon_file, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slot.section_id,
                    slot.sort_order,
                    slot.catalog_item_id,
                    slot.display_name,
                    slot.name_norm,
                    slot.icon_file,
                    now.isoformat(),
                ),
            )
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is set right after a successful INSERT
        return new_id

    async def next_sort_order(self, section_id: int) -> int:
        """Return the sort order that appends a new slot to the end of a section.

        Args:
            section_id: The section to append to.
        """
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS last FROM poster_slots WHERE section_id = ?",
            (section_id,),
        )
        row = await cursor.fetchone()
        return int(row["last"]) + 1 if row is not None else 0

    async def set_icon(self, slot_id: int, icon_file: str, *, now: datetime) -> None:
        """Point a slot at a different icon file.

        Args:
            slot_id: The slot to update.
            icon_file: New bare filename.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE poster_slots SET icon_file = ?, updated_at = ? WHERE id = ?",
                (icon_file, now.isoformat(), slot_id),
            )

    async def move_slot(
        self, slot_id: int, *, section_id: int, sort_order: int, now: datetime
    ) -> None:
        """Move a slot to a position, possibly in another section.

        Args:
            slot_id: The slot to move.
            section_id: Section it should end up in.
            sort_order: Position within that section.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE poster_slots SET section_id = ?, sort_order = ?, updated_at = ? "
                "WHERE id = ?",
                (section_id, sort_order, now.isoformat(), slot_id),
            )

    async def delete_slot(self, slot_id: int) -> PosterSlotRow | None:
        """Delete a slot, returning what was removed.

        Args:
            slot_id: The slot to delete.
        """
        cursor = await self._conn.execute("SELECT * FROM poster_slots WHERE id = ?", (slot_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        async with transaction(self._conn):
            await self._conn.execute("DELETE FROM poster_slots WHERE id = ?", (slot_id,))
        return _row_to_slot(row)

    async def delete_slots_for_item(self, catalog_item_id: int) -> int:
        """Delete every slot bound to a catalog item, returning how many went.

        Args:
            catalog_item_id: The catalog item whose slots to remove.
        """
        slots = await self.slots_for_item(catalog_item_id)
        if not slots:
            return 0
        async with transaction(self._conn):
            await self._conn.execute(
                "DELETE FROM poster_slots WHERE catalog_item_id = ?", (catalog_item_id,)
            )
        return len(slots)


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _row_to_section(row: aiosqlite.Row) -> PosterSectionRow:
    return PosterSectionRow(
        id=row["id"],
        poster_kind=PosterKind(row["poster_kind"]),
        name=row["name"],
        sort_order=row["sort_order"],
        columns=row["columns"],
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
    )


def _row_to_slot(row: aiosqlite.Row) -> PosterSlotRow:
    return PosterSlotRow(
        id=row["id"],
        section_id=row["section_id"],
        sort_order=row["sort_order"],
        catalog_item_id=row["catalog_item_id"],
        display_name=row["display_name"],
        name_norm=row["name_norm"],
        icon_file=row["icon_file"],
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
    )
