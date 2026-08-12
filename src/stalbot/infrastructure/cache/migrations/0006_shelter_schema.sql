-- Shelter (crafting cost) schema (sqlite_migration.md §IV.3, §IV.4; Э5).
--
-- `shelter_items` was already declared in migration 0005, not here: SQLite
-- cannot `ALTER TABLE ... ADD CONSTRAINT`, and `catalog_items.shelter_item_id`
-- (also 0005) references it. Everything else the shelter model needs is
-- declared here, additive same as 0005 — nothing in the legacy schema or
-- 0005's trading schema is touched.
--
-- All prices in this file are kopecks (§III.3): energy at 0.86 ₽/unit is a
-- 16% rounding error at whole-ruble precision on a typical 900-unit craft.

CREATE TABLE IF NOT EXISTS shelter_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Seeded by scripts/import_shelter.py: energy_price_kopeks, sale_bonus_pct,
-- count_ingredient_sale (§II.1 "Инструкция").

CREATE TABLE IF NOT EXISTS professions (
    key   TEXT    PRIMARY KEY,
    name  TEXT    NOT NULL,
    level INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 5)
);
-- 8 rows: ammo|pyro|armor|engineering|cooking|moonshine|medicine|materials.

-- No UNIQUE(output, profession): 27 items have multiple recipes within one
-- profession (§II.2 — "Ковёр" alone has 5).
CREATE TABLE IF NOT EXISTS recipes (
    id             INTEGER PRIMARY KEY,
    output_item_id INTEGER NOT NULL REFERENCES shelter_items(id) ON DELETE CASCADE,
    profession_key TEXT    NOT NULL REFERENCES professions(key),
    source_sheet   TEXT,
    source_cell    TEXT
);
CREATE INDEX IF NOT EXISTS ix_recipes_output ON recipes(output_item_id);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    recipe_id          INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_item_id INTEGER NOT NULL REFERENCES shelter_items(id),
    quantity            REAL    NOT NULL CHECK (quantity > 0),
    position            INTEGER NOT NULL,
    PRIMARY KEY (recipe_id, position)
);
-- Keyed by position, not ingredient: row order matters for tracing back to
-- the sheet, and one recipe can list the same ingredient twice.

CREATE TABLE IF NOT EXISTS recipe_yields (
    recipe_id       INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 5),
    units_per_craft REAL    NOT NULL CHECK (units_per_craft >= 0),
    PRIMARY KEY (recipe_id, level)
);
-- A recipe is available at a level iff units_per_craft > 0 there — this is
-- the sheet's "Низкий уровень" (§II.2), represented as a real zero instead
-- of a sentinel string.

CREATE TABLE IF NOT EXISTS shelter_cost (
    shelter_item_id    INTEGER PRIMARY KEY REFERENCES shelter_items(id) ON DELETE CASCADE,
    cost_kopeks        INTEGER,
    best_recipe_id     INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    source             TEXT NOT NULL CHECK (source IN ('my_price','market','crafted','unresolved')),
    depth              INTEGER NOT NULL DEFAULT 0,
    note               TEXT,
    calculator_version INTEGER NOT NULL,
    computed_at        TEXT NOT NULL
);
