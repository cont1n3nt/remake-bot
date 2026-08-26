-- Coupons (заявка 26.08.2026): a percent-off code, applied once per ticket
-- and locked onto the session at apply time (`coupon_discount_percent`) so
-- a later `/coupon_disable` or edit never retroactively changes an
-- already-applied discount. `coupon_redemptions` is the "one redemption per
-- person" enforcement (`UNIQUE(coupon_id, discord_id)`), not just a log.

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
    coupon_discount_percent TEXT
);
INSERT INTO ticket_sessions_new (
    channel_id, kind, author_id, status, delivery_method, game_nick,
    referrer_nick, referrer_discord_id, deadline, screenshot_url,
    screenshot_message_id, summary_message_id, panel_message_id,
    ocr_status, ocr_analysis_id, idempotency_key, created_at, updated_at,
    active_order_item_id
)
SELECT
    channel_id, kind, author_id, status, delivery_method, game_nick,
    referrer_nick, referrer_discord_id, deadline, screenshot_url,
    screenshot_message_id, summary_message_id, panel_message_id,
    ocr_status, ocr_analysis_id, idempotency_key, created_at, updated_at,
    active_order_item_id
FROM ticket_sessions;
DROP TABLE ticket_sessions;
ALTER TABLE ticket_sessions_new RENAME TO ticket_sessions;

CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    discount_percent TEXT NOT NULL,
    max_uses INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
    id INTEGER PRIMARY KEY,
    coupon_id INTEGER NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL,
    discord_id INTEGER NOT NULL,
    redeemed_at TEXT NOT NULL,
    UNIQUE (coupon_id, discord_id)
);
