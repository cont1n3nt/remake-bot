-- SQLite cache schema (see PLAN.md §8.1).
-- Every statement is idempotent (IF NOT EXISTS) so re-running this file on
-- an already-migrated database is always safe.
--
-- ★ Not authoritative (sqlite_migration.md §X, Э2): the real, versioned
-- source of truth is now `migrations/*.sql`, applied in order and tracked
-- via `PRAGMA user_version`. This file is a human-readable dump of the
-- cumulative result — `test_migrations_reproduce_schema_sql` in
-- `tests/unit/infrastructure/cache/migrations/` pins the two together, so
-- letting them drift fails CI rather than silently going stale.

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    category TEXT NOT NULL,
    price_buy TEXT,
    price_sell TEXT,
    emoji TEXT,
    updated_at TEXT,
    sheet_row INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_items_name_cat ON items (name_norm, category);

CREATE TABLE IF NOT EXISTS users (
    nick_norm TEXT PRIMARY KEY,
    nick_display TEXT NOT NULL,
    discord_id INTEGER,
    coins INTEGER NOT NULL DEFAULT 0,
    xp INTEGER NOT NULL DEFAULT 0,
    buy_turnover TEXT,
    sell_turnover TEXT,
    total_turnover TEXT,
    referrals_count INTEGER NOT NULL DEFAULT 0,
    is_booster INTEGER NOT NULL DEFAULT 0,
    rank TEXT,
    referral_role TEXT,
    sheet_row INTEGER NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_users_discord ON users (discord_id);

CREATE TABLE IF NOT EXISTS transactions (
    sheet_row INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    nick_norm TEXT NOT NULL,
    deal_type TEXT NOT NULL,
    amount TEXT NOT NULL,
    coins INTEGER,
    xp INTEGER,
    referrer_norm TEXT
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions (occurred_at);
CREATE INDEX IF NOT EXISTS ix_tx_nick ON transactions (nick_norm);

-- Drives promotion detection: the last-announced rank/referral role per
-- player, so ProgressionService (M3) never sends the same congratulation twice.
-- manual_rank_role: set by /set_rank (M8) — while true, the background
-- poller leaves the rank ladder alone entirely (PLAN.md §10.12).
CREATE TABLE IF NOT EXISTS progression_state (
    nick_norm TEXT PRIMARY KEY,
    last_rank TEXT,
    last_referral_role TEXT,
    manual_rank_role INTEGER NOT NULL DEFAULT 0,
    announced_at TEXT
);

-- Persistent ticket flow state (M9/M10). Created now so those milestones
-- need no schema migration; ocr_status/ocr_analysis_id are the M13 groundwork.
CREATE TABLE IF NOT EXISTS ticket_sessions (
    channel_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    delivery_method TEXT,
    game_nick TEXT,
    referrer_nick TEXT,
    referrer_discord_id INTEGER,
    deadline TEXT,
    screenshot_url TEXT,
    screenshot_message_id INTEGER,
    summary_message_id INTEGER,
    panel_message_id INTEGER,
    ocr_status TEXT NOT NULL DEFAULT 'disabled',
    ocr_analysis_id INTEGER,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active_order_item_id INTEGER
);

-- Boost-order draft lines (M10). item_name_norm + category let a line
-- survive /del_item renumbering the underlying item_id.
CREATE TABLE IF NOT EXISTS boost_order_lines (
    channel_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    item_name_norm TEXT NOT NULL,
    category TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (channel_id, item_id)
);

-- ★ OCR groundwork (M13, PLAN.md §11.8). Created from day one so every
-- ticket screenshot from M9 onward already has somewhere to record itself,
-- building the training dataset well before OCR is implemented.
CREATE TABLE IF NOT EXISTS screenshot_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    image_sha256 TEXT NOT NULL,
    image_url TEXT,
    sample_path TEXT,
    width INTEGER,
    height INTEGER,
    size_bytes INTEGER,
    mime TEXT,
    engine TEXT,
    status TEXT NOT NULL,
    raw_text TEXT,
    items_json TEXT,
    total_estimate TEXT,
    confidence REAL,
    duration_ms INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_shots_channel ON screenshot_analyses (channel_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_shots_sha ON screenshot_analyses (image_sha256);

-- Sync bookkeeping: last_full_sync, last_tx_row, ... `schema_version` used
-- to live here too — superseded by `PRAGMA user_version` (see db.py).
CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Write idempotency (M4, PLAN.md §7.4 step 5): keyed by e.g. a Discord
-- interaction id or a ticket's channel_id+message_id+user_id, so a retried
-- write (not a deliberate second /add) cannot create a duplicate Тикеты row.
-- Shared by TransactionService.register() for both /add and ticket confirmation.
CREATE TABLE IF NOT EXISTS write_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    deal_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- --- Trading schema (sqlite_migration.md §IV.1/§IV.2, Э3, migration 0005) ---
-- Additive: coexists with the legacy tables above until a future migration
-- drops them (§X). See migrations/0005_trading_schema.sql for the full
-- rationale, including why the new catalog table is `catalog_items`, not
-- `items` (name collision with the legacy table above).

CREATE TABLE IF NOT EXISTS players (
    id                 INTEGER PRIMARY KEY,
    nick_norm          TEXT    NOT NULL,
    nick_display       TEXT    NOT NULL,
    discord_id         INTEGER,
    referrer_player_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
    is_booster         INTEGER NOT NULL DEFAULT 0 CHECK (is_booster IN (0,1)),
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    CHECK (referrer_player_id IS NULL OR referrer_player_id <> id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_players_nick    ON players(nick_norm);
CREATE UNIQUE INDEX IF NOT EXISTS ux_players_discord ON players(discord_id) WHERE discord_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_players_referrer       ON players(referrer_player_id);

CREATE TABLE IF NOT EXISTS deals (
    id               INTEGER PRIMARY KEY,
    player_id        INTEGER NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    occurred_at      TEXT    NOT NULL,
    occurred_at_kind TEXT    NOT NULL DEFAULT 'unknown'
                     CHECK (occurred_at_kind IN
                            ('unknown','sheet_text','sheet_date','sheet_interpolated','bot')),
    deal_type        TEXT    NOT NULL CHECK (deal_type IN ('purchase','sale')),
    amount           INTEGER NOT NULL CHECK (amount >= 0),
    coins            INTEGER NOT NULL,
    xp               INTEGER NOT NULL,
    rank_at_deal     TEXT,
    booster_at_deal  INTEGER NOT NULL DEFAULT 0 CHECK (booster_at_deal IN (0,1)),
    recorded_by      INTEGER,
    source           TEXT    NOT NULL CHECK (source IN ('add','ticket','import')),
    legacy_sheet_row INTEGER,
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_deals_player   ON deals(player_id);
CREATE INDEX IF NOT EXISTS ix_deals_occurred ON deals(occurred_at);
CREATE INDEX IF NOT EXISTS ix_deals_agg      ON deals(player_id, deal_type, amount);

CREATE TABLE IF NOT EXISTS coin_ledger (
    id INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    delta      INTEGER NOT NULL CHECK (delta <> 0),
    reason     TEXT    NOT NULL,
    created_by INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_coin_ledger_player ON coin_ledger(player_id);

CREATE TABLE IF NOT EXISTS player_progression (
    player_id          INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    purchase_turnover  INTEGER NOT NULL,
    sale_turnover      INTEGER NOT NULL,
    total_turnover     INTEGER NOT NULL,
    referral_count     INTEGER NOT NULL,
    coins              INTEGER NOT NULL,
    xp                 INTEGER NOT NULL,
    rank_key           TEXT,
    referral_role_key  TEXT,
    breakdown_json     TEXT    NOT NULL,
    calculator_version INTEGER NOT NULL,
    computed_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS shelter_items (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    name_norm       TEXT    NOT NULL,
    kind            TEXT    NOT NULL CHECK (kind IN ('component','craftable','virtual')),
    market_kopeks   INTEGER,
    my_kopeks       INTEGER,
    vendor_kopeks   INTEGER,
    updated_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_shelter_name ON shelter_items(name_norm);

CREATE TABLE IF NOT EXISTS catalog_items (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    name_norm  TEXT    NOT NULL,
    category   TEXT    NOT NULL CHECK (category IN ('resource','boost')),
    section    TEXT,
    price_buy  INTEGER,
    price_sell INTEGER,
    emoji      TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    shelter_item_id INTEGER REFERENCES shelter_items(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT,
    deleted_at TEXT,
    CHECK (category <> 'resource' OR price_sell IS NULL),
    CHECK (category <> 'boost'    OR price_buy  IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_catalog_items_name_cat
    ON catalog_items(name_norm, category) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_catalog_items_shelter ON catalog_items(shelter_item_id);

CREATE TABLE IF NOT EXISTS item_price_history (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('buy','sell')),
    old_price INTEGER,
    new_price INTEGER,
    changed_by INTEGER,
    source TEXT NOT NULL CHECK (source IN ('setprice','import','catalog','migration')),
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_price_history_item ON item_price_history(item_id, changed_at);

-- --- Shelter (crafting cost) schema (sqlite_migration.md §IV.3/§IV.4, Э5, migration 0006) ---
-- All prices here are kopecks (§III.3). `shelter_items` is declared in the
-- trading-schema block above (migration 0005), not here — see that
-- migration's own comment for why.

CREATE TABLE IF NOT EXISTS shelter_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS professions (
    key   TEXT    PRIMARY KEY,
    name  TEXT    NOT NULL,
    level INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 5)
);

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

CREATE TABLE IF NOT EXISTS recipe_yields (
    recipe_id       INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 5),
    units_per_craft REAL    NOT NULL CHECK (units_per_craft >= 0),
    PRIMARY KEY (recipe_id, level)
);

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
