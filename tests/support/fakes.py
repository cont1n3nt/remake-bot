"""Fake `Protocol`-port implementations shared across service tests (sqlite_migration.md Часть XI).

Moved out of `test_progression.py` (the only file that used them) so a
future test that also needs a role/audit/channel double doesn't have to
duplicate them the way `FakeClock` was duplicated across 9 files.
"""

from stalbot.application.ports.role_gateway import RoleDiff, RoleSet


class FakeRoleGateway:
    """Records every `sync_roles` call and reports every desired role as granted."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[int, RoleSet]] = []

    async def sync_roles(self, member_id: int, target: RoleSet) -> RoleDiff:
        """Log the call and report `target.desired` as fully granted."""
        self.calls.append((member_id, target))
        return RoleDiff(granted=tuple(target.desired), revoked=())


class FakeAuditGateway:
    """Records every batch passed to `send_batch` instead of delivering it."""

    def __init__(self) -> None:
        """Start with an empty batch log."""
        self.batches: list[list[object]] = []

    async def send_batch(self, embeds: list[object]) -> None:
        """Record `embeds` as a delivered batch."""
        self.batches.append(list(embeds))


class FakeChannel:
    """Records every embed passed to `send` instead of delivering it."""

    def __init__(self, name: str = "general") -> None:
        """Name the channel and start with an empty send log.

        Args:
            name: Channel name, as `discord.abc.Messageable`-adjacent code expects.
        """
        self.name = name
        self.sent: list[object] = []

    async def send(self, *, embed: object) -> None:
        """Record `embed` as sent."""
        self.sent.append(embed)
