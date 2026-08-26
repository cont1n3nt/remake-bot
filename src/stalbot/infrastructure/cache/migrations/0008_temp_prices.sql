-- `/temp_price` (заявка 21.08.2026 п.9): a price override that expires and
-- reverts itself. `temp_prices` remembers the price to revert *to*
-- (`original_price`, `NULL` if the item had none) and *when*
-- (`expires_at`) — the live override itself is just a normal
-- `catalog_items.price_buy`/`price_sell` write, same as `/setprice`.
--
-- `item_price_history.source` gets a `'temp_price'` value for both the
-- apply and the revert entry — SQLite can't `ALTER ... CHECK`, so the
-- table is rebuilt (Э2's own `sqlite_master`-comparison test enforces this
-- migration and `schema.sql` end up byte-identical).

CREATE TABLE IF NOT EXISTS item_price_history_new (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('buy','sell')),
    old_price INTEGER,
    new_price INTEGER,
    changed_by INTEGER,
    source TEXT NOT NULL CHECK (source IN ('setprice','import','catalog','migration','temp_price')),
    changed_at TEXT NOT NULL
);
INSERT INTO item_price_history_new SELECT * FROM item_price_history;
DROP TABLE item_price_history;
ALTER TABLE item_price_history_new RENAME TO item_price_history;
CREATE INDEX IF NOT EXISTS ix_price_history_item ON item_price_history(item_id, changed_at);

CREATE TABLE IF NOT EXISTS temp_prices (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('buy','sell')),
    original_price INTEGER,
    expires_at TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_temp_prices_expires ON temp_prices(expires_at);
