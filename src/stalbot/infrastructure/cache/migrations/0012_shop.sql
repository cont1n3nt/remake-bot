-- Магазин за Coins (заявка 13.09.2026 п.2).
--
-- `coin_ledger` has been sitting empty since migration 0005, declared for
-- exactly this ("Signed: negative = spend (Магазин, Э12+)") and already
-- wired into the progression aggregation — so a purchase is a negative
-- ledger row plus a recompute, and the player's balance in `/profile` moves
-- on its own. Nothing about how Coins are earned changes here.
--
-- `xp_ledger` is new and mirrors it. Two of the assortment's effects grant
-- XP outright («Сухой паёк» +75 XP) or boost the XP a deal pays out
-- («Торговая гильдия» +50%), and XP had no adjustment channel at all —
-- `player_progression.xp` is derived from turnover by the calculator. A
-- time-windowed multiplier over deals would have meant teaching the
-- calculator about effects and their date ranges; instead the bonus is
-- written here as a normal credit at the moment it is earned, which keeps
-- the calculator untouched and leaves an auditable row per grant.

CREATE TABLE IF NOT EXISTS xp_ledger (
    id         INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    delta      INTEGER NOT NULL CHECK (delta <> 0),
    reason     TEXT    NOT NULL,
    created_by INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_xp_ledger_player ON xp_ledger(player_id);

-- The owner's own four tiers («Мелкие товары», «Средний класс», «Крупный
-- бизнес», «Элитные привилегии»), editable rather than hard-coded — the
-- whole point of п.2 is that the assortment stops being a code change.
CREATE TABLE IF NOT EXISTS shop_categories (
    key         TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT
);

-- `effect_kind` is deliberately not a CHECK list: a new kind of perk is
-- exactly the thing the owner invents between deploys, and a CHECK would
-- make each one a migration. Unknown kinds are stored and shown, and the
-- application layer decides which ones it knows how to apply automatically
-- (`domain/shop/effects.py`).
--
-- `effect_value` is free text, read according to `effect_kind`: a percent
-- for the discount/markup kinds, an amount for the grants, a JSON object
-- for the two-sided «Золотой абонемент». Kept as TEXT rather than split
-- into typed columns because half the kinds would leave the other half's
-- columns NULL, and the parsing rule belongs with the kind anyway.
--
-- `uses`/`duration_days`: NULL means "not limited that way". A разовый item
-- is uses=1; «на 7 дней» is duration_days=7; «на 3 сделки» is uses=3.
CREATE TABLE IF NOT EXISTS shop_items (
    id               INTEGER PRIMARY KEY,
    category_key     TEXT    NOT NULL REFERENCES shop_categories(key) ON DELETE RESTRICT,
    name             TEXT    NOT NULL,
    name_norm        TEXT    NOT NULL,
    description      TEXT    NOT NULL,
    price_coins      INTEGER NOT NULL CHECK (price_coins > 0),
    effect_kind      TEXT    NOT NULL,
    effect_value     TEXT,
    duration_days    INTEGER CHECK (duration_days IS NULL OR duration_days > 0),
    uses             INTEGER CHECK (uses IS NULL OR uses > 0),
    stock            INTEGER CHECK (stock IS NULL OR stock >= 0),
    per_player_limit INTEGER CHECK (per_player_limit IS NULL OR per_player_limit > 0),
    emoji            TEXT,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    active           INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at       TEXT    NOT NULL,
    updated_at       TEXT,
    deleted_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_shop_items_name
    ON shop_items(name_norm) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_shop_items_category ON shop_items(category_key, sort_order);

-- `price_coins` is a snapshot, not a join: raising a price tomorrow must not
-- change what yesterday's refund pays back.
--
-- `idempotency_key` is the Discord interaction id of the confirming click,
-- so a double-click or a retried interaction can't charge twice — the same
-- guard `write_idempotency` already gives deals.
CREATE TABLE IF NOT EXISTS shop_purchases (
    id              INTEGER PRIMARY KEY,
    player_id       INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    shop_item_id    INTEGER NOT NULL REFERENCES shop_items(id) ON DELETE RESTRICT,
    price_coins     INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','used','expired','refunded')),
    purchased_at    TEXT    NOT NULL,
    refunded_at     TEXT,
    refunded_by     INTEGER,
    idempotency_key TEXT UNIQUE,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_shop_purchases_player ON shop_purchases(player_id, status);
CREATE INDEX IF NOT EXISTS ix_shop_purchases_item   ON shop_purchases(shop_item_id);

-- What a purchase actually attached to the player — «прикрепляет это на
-- пользователя». Separate from `shop_purchases` because the purchase is a
-- financial fact that never changes, while the effect is live state that
-- expires, gets consumed, and is what every other part of the bot asks
-- about ("does this player have a discount right now?").
--
-- `uses_left` NULL = not counted by uses; `expires_at` NULL = no deadline.
-- An effect with both NULL is permanent until refunded.
CREATE TABLE IF NOT EXISTS player_effects (
    id           INTEGER PRIMARY KEY,
    player_id    INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    purchase_id  INTEGER REFERENCES shop_purchases(id) ON DELETE CASCADE,
    effect_kind  TEXT    NOT NULL,
    effect_value TEXT,
    uses_left    INTEGER CHECK (uses_left IS NULL OR uses_left >= 0),
    expires_at   TEXT,
    note         TEXT,
    created_at   TEXT    NOT NULL,
    consumed_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_player_effects_live
    ON player_effects(player_id, effect_kind) WHERE consumed_at IS NULL;
