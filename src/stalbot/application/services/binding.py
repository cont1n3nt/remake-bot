"""Shared "bind a Discord id to a player" write (sqlite_migration.md §IV.1, Э7).

Used by `TransactionService.register()` and `ManualGrantService.set_referral()`
— both need exactly the same conflict-check-then-write, so it lives here once
rather than being copied. Writes `players.discord_id` directly; there is no
Sheets column left to mirror it into.
"""

from stalbot.application.ports.clock import Clock
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.players import PlayersRepository


async def bind_discord(
    players: PlayersRepository,
    clock: Clock,
    nick: NormalizedNick,
    discord_id: int,
    *,
    force: bool,
) -> bool:
    """Bind `discord_id` to `nick`'s player row, unless already bound elsewhere.

    Args:
        players: Cache repository for player identity.
        clock: Time source for `updated_at`.
        nick: Normalized nick to bind.
        discord_id: Discord id to bind it to.
        force: Overwrite an existing different binding instead of no-op'ing.

    Returns:
        `True` if a write happened, `False` if the nick has no player row
        yet, is already bound to `discord_id`, or is bound to someone else
        and `force` is not set.
    """
    player = await players.get_by_nick(nick)
    if player is None or player.discord_id == discord_id:
        return False
    if player.discord_id is not None and not force:
        return False

    assert player.id is not None  # noqa: S101 - a fetched player always has a persisted id
    await players.set_discord_id(player.id, discord_id, now=clock.now())
    return True
