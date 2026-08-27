-- Coupon direction (заявка 27.08.2026 п.10): a coupon is either a discount
-- (заказ бустов only) or a markup (скупка/скуп only), never either. Table
-- rebuilds again — SQLite's plain `ALTER TABLE ... ADD COLUMN` would work
-- for a nullable column, but `coupons.kind` needs `NOT NULL DEFAULT`, and
-- the rebuild keeps this migration's resulting `sqlite_master.sql` text
-- readable/predictable to hand-mirror in `schema.sql` (Э2's own
-- reproduction test enforces the two stay identical).

CREATE TABLE IF NOT EXISTS coupons_new (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'discount' CHECK (kind IN ('discount','markup')),
    discount_percent TEXT NOT NULL,
    max_uses INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
INSERT INTO coupons_new (
    id, code, discount_percent, max_uses, used_count, active, created_by, created_at, expires_at
)
SELECT id, code, discount_percent, max_uses, used_count, active, created_by, created_at, expires_at
FROM coupons;
DROP TABLE coupons;
ALTER TABLE coupons_new RENAME TO coupons;

CREATE TABLE IF NOT EXISTS ticket_sessions_new (
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
    active_order_item_id INTEGER,
    coupon_code TEXT,
    coupon_discount_percent TEXT,
    coupon_kind TEXT
);
INSERT INTO ticket_sessions_new (
    channel_id, kind, author_id, status, delivery_method, game_nick,
    referrer_nick, referrer_discord_id, deadline, screenshot_url,
    screenshot_message_id, summary_message_id, panel_message_id,
    ocr_status, ocr_analysis_id, idempotency_key, created_at, updated_at,
    active_order_item_id, coupon_code, coupon_discount_percent
)
SELECT
    channel_id, kind, author_id, status, delivery_method, game_nick,
    referrer_nick, referrer_discord_id, deadline, screenshot_url,
    screenshot_message_id, summary_message_id, panel_message_id,
    ocr_status, ocr_analysis_id, idempotency_key, created_at, updated_at,
    active_order_item_id, coupon_code, coupon_discount_percent
FROM ticket_sessions;
DROP TABLE ticket_sessions;
ALTER TABLE ticket_sessions_new RENAME TO ticket_sessions;
