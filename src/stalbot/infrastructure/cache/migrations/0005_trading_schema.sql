-- Trading schema (sqlite_migration.md §IV.1, §IV.2, §IV.4; Э3).
--
-- Additive (§VII: "Э3 — Схема торговли и репозитории (аддитивно)"): the
-- legacy `items`/`users`/`transactions`/`progression_state` tables are
-- untouched here and keep serving the live bot until Э6 (reads) / Э7
-- (writes) switch over. They are dropped only in a future migration
-- (§X: "SCHEMA_VERSION: ... -> 7 (снос легаси-таблиц)").
--
-- ★ Deviation from the plan's literal §IV.2 code block, flagged for the
-- owner: that block names the new catalog table `items` — but a table of
-- that name already exists (the legacy Sheets-cache one, `schema.sql`),
-- and this migration is additive, so the names collide. The new table is
-- named `catalog_items` here instead; a later migration (after Э7, once
-- the legacy `items` table is actually dropped) can rename it to `items`
-- if desired. No other table name in §IV.1/§IV.2 collides with anything
-- in the legacy schema.
--
-- `shelter_items` is declared here (empty; Э5 populates it) rather than in
-- migration 0006 alongside the rest of the shelter schema, because SQLite
-- cannot `ALTER TABLE ... ADD CONSTRAINT` — `catalog_items.shelter_item_id`
-- needs the table to reference to already exist (§IV.4).

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

-- occurred_at_kind: 'sheet_interpolated' marks the 534 pre-26.06.2026
-- deals the sheet never recorded a real date for — evenly interpolated
-- between 26.06.2026 and the first real date (27.07.26 21:31), per the
-- owner's confirmed order/lower-bound (sqlite_migration.md §I.3). The
-- presentation layer must mark those as "date approximate".
-- rank_at_deal/booster_at_deal: a snapshot of the player's status AT THE
-- MOMENT of the deal — rank unlocks forward-looking bonuses, a booster
-- gets a surcharge, both act going forward, not retroactively across all
-- history the way the sheet's formula effectively did (§XIV.1).
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

-- Signed: negative = spend (Магазин, Э12+), positive = grant. Empty until
-- Э12's shop exists; declared now so `ProgressionRepository`'s aggregation
-- query (§V.1) can already read it without a later ALTER.
CREATE TABLE IF NOT EXISTS coin_ledger (
    id INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    delta      INTEGER NOT NULL CHECK (delta <> 0),
    reason     TEXT    NOT NULL,
    created_by INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_coin_ledger_player ON coin_ledger(player_id);

-- Materialized progression (§III.3 "Прогрессия материализуется" — a
-- rank-up needs an atomic snapshot to announce against, and recomputing on
-- every read isn't free at scale even though today's dataset is tiny).
-- `rank_key`/`referral_role_key` store the ladder tier *key* (e.g.
-- "prestige"), not the sheet's emoji label — §III.3's "не label с эмодзи".
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

-- Empty stub for Э5 (sqlite_migration.md §IV.3/§IV.4) — only the FK target
-- for catalog_items.shelter_item_id needs to exist yet.
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

-- ★ Named `catalog_items`, not `items` — see the migration's header comment.
-- `category`: not a taxonomy, a deal side — resource = the bot buys, boost
-- = the bot sells (sqlite_migration.md §I.5). `section`: Кулинария /
-- Самогоноварение / Медицина / Пиротехника / Боеприпасы / Прочее, matches
-- the shelter professions (§I.7). `shelter_item_id`: bridge to the game
-- reference; NULL = not yet mapped/confirmed.
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
