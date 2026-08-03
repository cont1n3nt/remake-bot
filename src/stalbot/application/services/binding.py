"""Shared "bind a Discord id to a nick's row" write (column `I`, PLAN.md §6.1).

Used by `TransactionService.register()` (M4) and `ManualGrantService.set_referral()`
(M8) — both need exactly the same raw write plus cache update, so it lives
here once rather than being copied.
"""

from dataclasses import replace

from stalbot.application.ports.clock import Clock
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.sheets.a1 import a1_range
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.infrastructure.sheets.layouts import DATABASE_SHEET


async def bind_discord(
    users: UsersCacheRepository,
    sheets: SheetsClient,
    clock: Clock,
    nick: NormalizedNick,
    discord_id: int,
    *,
    force: bool,
) -> bool:
    """Bind `discord_id` to `nick`'s row in column `I`, unless already bound elsewhere.

    Args:
        users: Cache repository for the `Юзеры` block.
        sheets: Sheets access for the raw `I`-column write.
        clock: Time source for the cache's `synced_at` stamp.
        nick: Normalized nick to bind.
        discord_id: Discord id to bind it to.
        force: Overwrite an existing different binding instead of no-op'ing.

    Returns:
        `True` if a write happened, `False` if the nick has no cached
        profile yet, is already bound to `discord_id`, or is bound to
        someone else and `force` is not set.
    """
    profile = await users.get_by_nick(nick)
    if profile is None or profile.discord_id == discord_id:
        return False
    if profile.discord_id is not None and not force:
        return False

    ref = a1_range(DATABASE_SHEET, "I", profile.row)
    await sheets.batch_update({ref: [[discord_id]]})
    display = await users.get_nick_display(nick) or nick
    await users.upsert_many(
        [replace(profile, discord_id=discord_id)],
        nick_displays={nick: display},
        synced_at=clock.now().isoformat(),
    )
    return True
