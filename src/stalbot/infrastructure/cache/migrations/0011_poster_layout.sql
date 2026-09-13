-- Poster layout moves out of the package and into the database
-- (заявка 13.09.2026 п.6 и вторая половина п.13).
--
-- Until now the three posters were frozen JSON files shipped inside the
-- wheel (`src/stalbot/assets/posters/layout_*.json`), each entry pinning an
-- item's `name_norm` to an icon filename. That made adding an item to a
-- poster a code change: the owner cannot edit a file inside the running
-- image, and anything written there is lost on the next deploy. The layout
-- is data the owner changes weekly, so it belongs where the rest of the
-- data lives.
--
-- Icons themselves stay on disk rather than becoming BLOBs here:
-- `docker-compose.yml` already bind-mounts `./data:/app/data`, so
-- `data/poster_icons/` survives a redeploy, `PillowRenderer` already takes
-- a `Path`, and SQLite stays small enough to back up by copying.
--
-- `scripts/import_poster_layouts.py` fills both tables from the existing
-- JSON (224 slots) and copies the icons across — nothing is lost in the
-- move, and the JSON stays in the repo as the import's source.

-- One row per section block on a poster. `name` is NULL for the unnamed
-- column-blocks «Скуп ресурсов» and «Скуп ваших бустов» are built from —
-- those sheets have no header row at all, and their "sections" are the
-- source spreadsheet's real columns.
CREATE TABLE IF NOT EXISTS poster_sections (
    id          INTEGER PRIMARY KEY,
    poster_kind TEXT    NOT NULL
                CHECK (poster_kind IN ('resources','boosts','boost_purchases')),
    name        TEXT,
    sort_order  INTEGER NOT NULL,
    columns     INTEGER NOT NULL DEFAULT 1 CHECK (columns >= 1),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_poster_sections_kind ON poster_sections(poster_kind, sort_order);

-- One row per item slot inside a section.
--
-- `catalog_item_id` is nullable and `ON DELETE SET NULL` on purpose: a
-- slot can legitimately outlive its catalog row (an item removed from the
-- catalog but still drawn on a poster the owner has not re-rendered yet),
-- and the imported slots include names the live catalog does not have at
-- all. `name_norm` is kept alongside as the fallback match, which is how
-- the JSON layout resolved every slot before this migration — a slot with
-- no id still finds its item exactly the way it used to.
--
-- `icon_file` is a bare filename inside the poster-icons directory, not a
-- path: the directory is configuration (`Settings.poster_icons_dir`), and
-- storing a path would bake one deployment's layout into the data.
CREATE TABLE IF NOT EXISTS poster_slots (
    id              INTEGER PRIMARY KEY,
    section_id      INTEGER NOT NULL REFERENCES poster_sections(id) ON DELETE CASCADE,
    sort_order      INTEGER NOT NULL,
    catalog_item_id INTEGER REFERENCES catalog_items(id) ON DELETE SET NULL,
    display_name    TEXT    NOT NULL,
    name_norm       TEXT    NOT NULL,
    icon_file       TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_poster_slots_section ON poster_slots(section_id, sort_order);
CREATE INDEX IF NOT EXISTS ix_poster_slots_item    ON poster_slots(catalog_item_id);
