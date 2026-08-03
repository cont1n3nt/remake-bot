"""`RoleGateway` backed by a real Discord guild (PLAN.md §3.1, §9.2)."""

import logging

import discord

from stalbot.application.ports.role_gateway import RoleDiff, RoleSet

logger = logging.getLogger(__name__)


class DiscordRoleGateway:
    """Grants/revokes roles on a member of the configured guild.

    The guild and member are resolved on every call rather than cached, so
    a transient miss right after startup is retried instead of permanently
    breaking role sync for the rest of the process — the same tradeoff
    `AuditChannelGateway` makes for the log channel.
    """

    def __init__(self, client: discord.Client, guild_id: int) -> None:
        """Create the gateway.

        Args:
            client: The running bot, used to resolve the guild/member.
            guild_id: `Settings.guild_id`.
        """
        self._client = client
        self._guild_id = guild_id

    async def sync_roles(self, member_id: int, target: RoleSet) -> RoleDiff:
        """Grant/revoke roles so the member ends up holding exactly `target.desired`.

        A member who has left the server, or a guild the bot can no longer
        see, is logged and treated as a no-op rather than raised — a
        background poll over the whole player base must not die on one
        stale entry (PLAN.md §9.2's `tasks.loop(minutes=5)`).

        Args:
            member_id: Discord member id.
            target: Desired roles and the universe `sync_roles` may revoke from.

        Returns:
            The roles actually granted/revoked.
        """
        guild = self._client.get_guild(self._guild_id)
        if guild is None:
            logger.warning("role sync skipped: guild %s not visible to the bot", self._guild_id)
            return RoleDiff(granted=(), revoked=())

        try:
            member = guild.get_member(member_id) or await guild.fetch_member(member_id)
        except discord.NotFound:
            logger.warning(
                "role sync skipped: member %s not found in guild %s", member_id, self._guild_id
            )
            return RoleDiff(granted=(), revoked=())

        current = frozenset(role.id for role in member.roles)
        to_grant = target.desired - current
        to_revoke = (current & target.universe) - target.desired

        try:
            if to_grant:
                await member.add_roles(
                    *(discord.Object(id=role_id) for role_id in to_grant), reason="progression sync"
                )
            if to_revoke:
                await member.remove_roles(
                    *(discord.Object(id=role_id) for role_id in to_revoke),
                    reason="progression sync",
                )
        except discord.Forbidden:
            logger.warning(
                "role sync failed for member %s: bot lacks permission (check role hierarchy)",
                member_id,
            )
            return RoleDiff(granted=(), revoked=())

        return RoleDiff(granted=tuple(to_grant), revoked=tuple(to_revoke))
