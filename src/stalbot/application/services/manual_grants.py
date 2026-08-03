"""`/set_referral`, `/set_rank` — hand-operated overrides outside the deal/formula flow.

(PLAN.md §10.12.)

Both bypass the mechanisms that normally decide these values: `/set_referral`
writes column `H` directly instead of that happening as a side effect of
`TransactionService.register()`, and `/set_rank` grants a Discord role
without ever touching the sheet — the rank column is a formula (decision
A2), so a "manual rank" is a role that intentionally does not match it.
"""

from dataclasses import replace

from stalbot.application.dto.manual_grant import SetRankResult, SetReferralResult
from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.ports.clock import Clock
from stalbot.application.ports.role_gateway import RoleGateway, RoleSet
from stalbot.application.services.binding import bind_discord
from stalbot.domain.errors import NoTransactionsYetError
from stalbot.domain.nick import NormalizedNick, normalize_nick
from stalbot.domain.progression.ranks import RankLadder, RankTier
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.sheets.a1 import a1_range
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.infrastructure.sheets.layouts import DATABASE_SHEET


class ManualGrantService:
    """Backs `/set_referral` and `/set_rank`."""

    def __init__(
        self,
        sheets: SheetsClient,
        transactions: TransactionsCacheRepository,
        users: UsersCacheRepository,
        progression_state: ProgressionStateRepository,
        roles: RoleGateway,
        *,
        clock: Clock,
        rank_ladder: RankLadder | None = None,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            sheets: Writes column `H` (`/set_referral`) and column `I`
                (Discord binding for both players).
            transactions: Finds a player's first `Тикеты` row.
            users: Discord-binding cache lookups/writes.
            progression_state: Tracks the `manual_rank_role` flag.
            roles: Grants/revokes the manually-assigned rank role.
            clock: Time source, tz-aware `GMT3`.
            rank_ladder: Defaults to a fresh `RankLadder()`.
        """
        self._sheets = sheets
        self._transactions = transactions
        self._users = users
        self._progression_state = progression_state
        self._roles = roles
        self._clock = clock
        self._rank_ladder = rank_ladder or RankLadder()

    async def current_referrer(self, nick: str) -> NormalizedNick | None:
        """Return the referrer already on record for `nick`'s first deal, if any.

        Args:
            nick: Game nick, any casing.
        """
        records = await self._transactions.list_by_nick(normalize_nick(nick))
        return records[0].referrer if records else None

    async def set_referral(
        self,
        nick: str,
        referrer_nick: str,
        discord_id: int,
        referrer_discord_id: int,
    ) -> SetReferralResult:
        """Write `referrer_nick` into `nick`'s first `Тикеты` row (PLAN.md §10.12).

        Args:
            nick: The referred player's game nick.
            referrer_nick: The referrer's game nick.
            discord_id: Discord id to bind to `nick`.
            referrer_discord_id: Discord id to bind to `referrer_nick`.

        Returns:
            What was written and which bindings changed.

        Raises:
            NoTransactionsYetError: If `nick` has no recorded deals yet.
        """
        nick_norm = normalize_nick(nick)
        referrer_norm = normalize_nick(referrer_nick)

        records = await self._transactions.list_by_nick(nick_norm)
        if not records:
            raise NoTransactionsYetError(f"{nick_norm!r} has no recorded deals yet")
        first = records[0]

        ref = a1_range(DATABASE_SHEET, "H", first.row)
        await self._sheets.write_verified({ref: [[referrer_nick]]})
        await self._transactions.upsert_many([replace(first, referrer=referrer_norm)])

        player_bound = await bind_discord(
            self._users, self._sheets, self._clock, nick_norm, discord_id, force=False
        )
        referrer_bound = await bind_discord(
            self._users, self._sheets, self._clock, referrer_norm, referrer_discord_id, force=False
        )

        return SetReferralResult(
            row=first.row,
            previous_referrer=first.referrer,
            player_discord_bound=player_bound,
            referrer_discord_bound=referrer_bound,
        )

    async def set_rank(
        self, nick: str, discord_id: int, tier: RankTier, *, revoke: bool
    ) -> SetRankResult:
        """Grant or revoke a manually-assigned rank role. The sheet is never touched.

        Args:
            nick: Game nick the role is tracked against (`progression_state` key).
            discord_id: Discord member to grant/revoke the role for.
            tier: The rank tier to grant (or, on `revoke`, take away).
            revoke: `True` toggles the role off instead of granting it — a
                repeat `/set_rank` call with the same rank (PLAN.md §10.12).

        Returns:
            The tier acted on and whether it ended up granted or revoked.
        """
        nick_norm = normalize_nick(nick)
        desired = frozenset() if revoke else frozenset({tier.role_id})
        await self._roles.sync_roles(
            discord_id, RoleSet(desired=desired, universe=self._rank_ladder.role_ids)
        )

        previous = await self._progression_state.get(nick_norm)
        await self._progression_state.upsert(
            ProgressionState(
                nick=nick_norm,
                last_rank=previous.last_rank if previous else None,
                last_referral_role=previous.last_referral_role if previous else None,
                manual_rank_role=not revoke,
                announced_at=previous.announced_at if previous else None,
            )
        )
        return SetRankResult(tier=tier, granted=not revoke)
