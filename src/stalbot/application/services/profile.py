"""`/profile` and `/referrals` — lookup with the binding check (PLAN.md §10.2, §10.3).

Both commands share the same access rule: a player may only look up their
own profile unless the requester is an admin and `ADMIN_CAN_VIEW_ANY_PROFILE`
allows it. Centralizing that check here (rather than duplicating it in both
cog handlers) is the whole reason this service exists.

sqlite_migration.md Э6: reads `players`/`player_progression` instead of the
sheet-era `users`/`transactions` cache. `list_referrals` now reads
`players.referrer_player_id` directly (`PlayersRepository.list_by_referrer`)
instead of scanning `transactions.referrer_norm` — the referee's own row
carries the referrer, so no per-transaction scan is needed.
"""

from collections.abc import Sequence

from stalbot.application.dto.profile_view import ProfileView, ReferredPlayer
from stalbot.domain.errors import PlayerNotFoundError, ProfileAccessDeniedError
from stalbot.domain.nick import normalize_nick
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository


class ProfileService:
    """Looks up a player's profile and referral list from the cache."""

    def __init__(self, players: PlayersRepository, progression: ProgressionRepository) -> None:
        """Wire the service to its collaborators.

        Args:
            players: Cache repository for player identity.
            progression: Cache repository for materialized Coins/XP/rank.
        """
        self._players = players
        self._progression = progression

    async def get_profile(
        self, nick: str, *, requester_id: int, is_admin: bool, admin_can_view_any: bool
    ) -> ProfileView:
        """Look up a profile, enforcing the binding check (PLAN.md §10.2).

        Args:
            nick: The game nick, as typed by the requester.
            requester_id: Discord id of whoever ran the command.
            is_admin: Whether the requester holds administrator permissions.
            admin_can_view_any: The `ADMIN_CAN_VIEW_ANY_PROFILE` setting.

        Raises:
            PlayerNotFoundError: No such nick exists in the database.
            ProfileAccessDeniedError: The requester is not the profile's
                bound account, and is not an admin permitted to look up others.
        """
        nick_norm = normalize_nick(nick)
        player = await self._players.get_by_nick(nick_norm)
        if player is None:
            raise PlayerNotFoundError(nick)
        if not (is_admin and admin_can_view_any) and player.discord_id != requester_id:
            raise ProfileAccessDeniedError(nick)

        assert player.id is not None  # noqa: S101 - a fetched player always has a persisted id
        record = await self._progression.get(player.id)
        return ProfileView(player=player, progression=record, nick_display=player.nick_display)

    async def list_referrals(
        self, nick: str, *, requester_id: int, is_admin: bool, admin_can_view_any: bool
    ) -> tuple[ProfileView, Sequence[ReferredPlayer]]:
        """Look up a player's referral-role profile plus everyone they referred.

        Subject to the same binding check as `get_profile`.

        Args:
            nick: The game nick, as typed by the requester.
            requester_id: Discord id of whoever ran the command.
            is_admin: Whether the requester holds administrator permissions.
            admin_can_view_any: The `ADMIN_CAN_VIEW_ANY_PROFILE` setting.
        """
        view = await self.get_profile(
            nick,
            requester_id=requester_id,
            is_admin=is_admin,
            admin_can_view_any=admin_can_view_any,
        )

        assert view.player.id is not None  # noqa: S101 - see get_profile
        referred_players = await self._players.list_by_referrer(view.player.id)
        referred = [
            ReferredPlayer(nick_display=p.nick_display, discord_id=p.discord_id)
            for p in referred_players
        ]
        return view, referred
