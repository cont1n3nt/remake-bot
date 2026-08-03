"""DTO for `/logs` — the deal archive, paginated (PLAN.md §10.10)."""

from dataclasses import dataclass

from stalbot.domain.entities.transaction import TransactionRecord


@dataclass(frozen=True, slots=True)
class LogEntry:
    """A transaction plus its position within its own calendar day.

    `day_number` resets to `1` at each `00:00 GMT+3` boundary (PLAN.md
    §10.10) — it is unrelated to `transaction.row`, the sheet row.
    `discord_id` is not part of `TransactionRecord` itself (that entity
    mirrors the `Тикеты` block only); it is resolved separately here since
    `/logs` displays it alongside the nick.
    """

    day_number: int
    transaction: TransactionRecord
    discord_id: int | None
