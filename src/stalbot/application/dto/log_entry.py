"""DTO for `/logs` — the deal archive, paginated (PLAN.md §10.10; sqlite_migration.md Э6)."""

from dataclasses import dataclass

from stalbot.domain.entities.deal import Deal


@dataclass(frozen=True, slots=True)
class LogEntry:
    """A deal plus its position within its own calendar day and its display-cased nick.

    `day_number` resets to `1` at each `00:00` boundary of `occurred_at`'s
    own date (PLAN.md §10.10) — it is unrelated to `deal.id`. Neither
    `nick_display` nor `discord_id` are part of `Deal` itself (that entity
    only carries `player_id`, sqlite_migration.md §IV.1); both are resolved
    separately since `/logs` displays them alongside the deal.
    """

    day_number: int
    deal: Deal
    nick_display: str
    discord_id: int | None
