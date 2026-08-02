-- SQLite cache schema (see PLAN.md §8.1).
-- Every statement is idempotent (IF NOT EXISTS) so re-running this file on
-- an already-migrated database is always safe.

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
    updated_at TEXT NOT NULL
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

-- Sync bookkeeping: schema_version, last_full_sync, last_tx_row, ...
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
    sheet_row INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
