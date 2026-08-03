"""`/profile` and `/referrals` — lookup with the binding check (PLAN.md §10.2, §10.3).

Both commands share the same access rule: a player may only look up their
own profile unless the requester is an admin and `ADMIN_CAN_VIEW_ANY_PROFILE`
allows it. Centralizing that check here (rather than duplicating it in both
cog handlers) is the whole reason this service exists.
"""

from collections.abc import Sequence

from stalbot.application.dto.profile_view import ProfileView, ReferredPlayer
from stalbot.domain.errors import PlayerNotFoundError, ProfileAccessDeniedError
from stalbot.domain.nick import normalize_nick
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository


class ProfileService:
    """Looks up a player's profile and referral list from the cache."""

    def __init__(
        self, users: UsersCacheRepository, transactions: TransactionsCacheRepository
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            users: Cache repository for player profiles.
            transactions: Cache repository, used to list who a player referred.
        """
        self._users = users
        self._transactions = transactions

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
        profile = await self._users.get_by_nick(nick_norm)
        if profile is None:
            raise PlayerNotFoundError(nick)
        if not (is_admin and admin_can_view_any) and profile.discord_id != requester_id:
            raise ProfileAccessDeniedError(nick)

        display = await self._users.get_nick_display(nick_norm) or nick_norm
        return ProfileView(profile=profile, nick_display=display)

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

        referred_nicks = await self._transactions.list_referral_targets(view.profile.nick)
        referred: list[ReferredPlayer] = []
        for referred_nick in referred_nicks:
            referred_profile = await self._users.get_by_nick(referred_nick)
            display = await self._users.get_nick_display(referred_nick) or referred_nick
            referred.append(
                ReferredPlayer(
                    nick_display=display,
                    discord_id=(
                        referred_profile.discord_id if referred_profile is not None else None
                    ),
                )
            )
        return view, referred
