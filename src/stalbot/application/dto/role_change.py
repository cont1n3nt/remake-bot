"""`RoleChange` — one player's actual role grant/revoke from a forced resync."""

from dataclasses import dataclass

from stalbot.domain.nick import NormalizedNick


@dataclass(frozen=True, slots=True)
class RoleChange:
    """What `ProgressionService.resync_all` actually changed for one player."""

    nick: NormalizedNick
    discord_id: int
    granted: tuple[int, ...]
    revoked: tuple[int, ...]
