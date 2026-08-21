-- Repurpose `write_idempotency` for the SQLite write path (sqlite_migration.md
-- §IV.4, Э7): a replayed write now looks up a `deals.id`, not a Sheets row —
-- there is no "row" left once `TransactionService.register()` no longer
-- writes to a spreadsheet. `ALTER TABLE ... RENAME COLUMN` keeps any rows a
-- live bot has already written (none of the write-path services have cut
-- over before this migration ships, so in practice this table is empty, but
-- a plain rename is free either way and needs no data migration logic).
ALTER TABLE write_idempotency RENAME COLUMN sheet_row TO deal_id;
