"""Admin-only slash command guard (PLAN.md §5.5).

Every command except `/profile` and `/referrals` requires administrator
permissions. Two layers of defense: `default_member_permissions` hides the
command from non-admins in Discord's own UI, and a runtime check blocks
anyone who invokes it anyway (stale UI cache, direct API call).
"""

from collections.abc import Callable
from typing import TypeVar

import discord
from discord import app_commands

_CommandT = TypeVar("_CommandT", bound=Callable[..., object])


def admin_only() -> Callable[[_CommandT], _CommandT]:
    """Restrict a slash command to server administrators.

    Returns:
        A decorator applying both `default_member_permissions` (UI
        visibility) and a runtime `app_commands.check` (actual enforcement).
        A denied check raises `app_commands.CheckFailure`, handled by the
        global error handler in `presentation/errors.py`.
    """

    def decorator(func: _CommandT) -> _CommandT:
        with_permissions = app_commands.default_permissions(administrator=True)(func)
        return app_commands.check(_is_administrator)(with_permissions)

    return decorator


async def _is_administrator(interaction: discord.Interaction) -> bool:
    """Runtime check backing `admin_only()`."""
    member = interaction.user
    return isinstance(member, discord.Member) and member.guild_permissions.administrator
