"""Recording a deal (PLAN.md §7.4, §10.1; sqlite_migration.md Э7).

`TransactionService.register()` is the one place that ever inserts a new
`deals` row — used directly by `/add` and, unchanged, by ticket confirmation.
Coins/XP are computed on the spot by `domain.progression.calculator.
deal_reward()` (a pure function, exactly reproducing the sheet's `F`/`G`
formulas) instead of being written raw and read back after a recalculation
delay — there is no more delay to wait out.
"""

import asyncio
from dataclasses import replace

from stalbot.application.dto.transaction_request import (
    AddTransactionRequest,
    TransactionRegistrationResult,
)
from stalbot.application.ports.clock import Clock
from stalbot.domain.entities.deal import Deal
from stalbot.domain.enums import OccurredAtKind
from stalbot.domain.errors import DealNotFoundError
from stalbot.domain.money import to_storage
from stalbot.domain.nick import normalize_nick
from stalbot.domain.progression.calculator import deal_reward
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.idempotency import IdempotencyRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository


class TransactionService:
    """Inserts a deal, snapshots rank/booster status, and recomputes progression."""

    def __init__(
        self,
        players: PlayersRepository,
        deals: DealsRepository,
        progression: ProgressionRepository,
        idempotency: IdempotencyRepository,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            players: Cache repository for player identity/binding/referrer.
            deals: Cache repository for the deal log.
            progression: Recomputes Coins/XP/rank after the deal is inserted.
            idempotency: Prevents a retried write from duplicating a deal.
            clock: Time source, tz-aware `GMT3`.
        """
        self._players = players
        self._deals = deals
        self._progression = progression
        self._idempotency = idempotency
        self._clock = clock
        #: Serializes the whole replay-check -> insert -> record path
        #: (CLUSTER-1): two concurrent calls with the same idempotency key
        #: could otherwise both pass the replay check before either has
        #: recorded it and both insert a deal. Write throughput here is
        #: inherently low (admin actions), so serializing the whole method
        #: is cheap insurance, not a bottleneck.
        self._lock = asyncio.Lock()

    async def register(self, request: AddTransactionRequest) -> TransactionRegistrationResult:
        """Record one deal, idempotently.

        The entire method runs under a single lock (see `__init__`) so two
        concurrent calls — a double-clicked confirm button, two admins
        racing `/add` — can never both pass the idempotency replay check.

        Args:
            request: What to write and who it is for.

        Returns:
            The recorded deal plus what else the call did (bound a Discord id).
        """
        async with self._lock:
            return await self._register_locked(request)

    async def _register_locked(
        self, request: AddTransactionRequest
    ) -> TransactionRegistrationResult:
        replayed = await self._replay_if_seen(request.idempotency_key)
        if replayed is not None:
            return replayed

        nick = normalize_nick(request.nick)
        now = self._clock.now()
        stored_amount = to_storage(request.amount)

        player = await self._players.get_or_create(nick, request.nick, now=now)
        assert player.id is not None  # noqa: S101 - get_or_create always returns a persisted player

        existing_deals = await self._deals.for_player(player.id)
        is_first_deal = len(existing_deals) == 0
        write_referrer = (
            is_first_deal
            and request.referrer_nick is not None
            and player.referrer_player_id is None
        )

        referrer_player_id: int | None = None
        if write_referrer:
            assert request.referrer_nick is not None  # noqa: S101 - guarded by write_referrer
            referrer_nick = normalize_nick(request.referrer_nick)
            referrer = await self._players.get_or_create(
                referrer_nick, request.referrer_nick, now=now
            )
            assert referrer.id is not None  # noqa: S101 - get_or_create always returns a persisted player
            await self._players.set_referrer(player.id, referrer.id, now=now)
            referrer_player_id = referrer.id

        progression_record = await self._progression.get(player.id)
        reward = deal_reward(request.deal_type, int(stored_amount))

        deal = Deal(
            id=None,
            player_id=player.id,
            occurred_at=now,
            occurred_at_kind=OccurredAtKind.BOT,
            deal_type=request.deal_type,
            amount=stored_amount,
            coins=reward.coins,
            xp=reward.xp,
            rank_at_deal=progression_record.rank_key if progression_record is not None else None,
            booster_at_deal=player.is_booster,
            recorded_by=request.discord_id,
            source=request.source,
            legacy_sheet_row=None,
            created_at=now,
        )
        deal_id = await self._deals.insert(deal)
        deal = replace(deal, id=deal_id)

        await self._idempotency.record(request.idempotency_key, deal_id, created_at=now.isoformat())

        recompute_ids = {player.id} | ({referrer_player_id} if referrer_player_id else set())
        await self._progression.recompute(list(recompute_ids), now=now)

        discord_bound = await self._players.bind_discord(
            nick, request.discord_id, force=request.force_rebind, now=now
        )

        return TransactionRegistrationResult(
            deal=deal, nick_display=request.nick, discord_bound=discord_bound
        )

    async def get_deal(self, deal_id: int) -> Deal | None:
        """Look up a deal by id — used by `/del_deal` to render a confirmation.

        Args:
            deal_id: The deal's `deals.id`.
        """
        return await self._deals.get_by_id(deal_id)

    async def delete_deal(self, deal_id: int) -> Deal:
        """Delete a deal and immediately recompute the affected player's progression.

        Unlike `register()`, there is no idempotency concern — deleting an
        already-deleted id simply raises `DealNotFoundError`, so a retried
        call can't double-delete anything.

        Args:
            deal_id: The deal to remove (`/del_deal`, undoing a mis-entered `/add`).

        Returns:
            The deal as it was right before deletion, for the confirmation message.

        Raises:
            DealNotFoundError: No deal with this id exists.
        """
        deal = await self._deals.get_by_id(deal_id)
        if deal is None:
            raise DealNotFoundError(f"Сделка #{deal_id} не найдена.")

        await self._deals.delete(deal_id)
        await self._progression.recompute([deal.player_id], now=self._clock.now())
        return deal

    async def _replay_if_seen(self, key: str) -> TransactionRegistrationResult | None:
        cached_id = await self._idempotency.get(key)
        if cached_id is None:
            return None
        cached_deal = await self._deals.get_by_id(cached_id)
        if cached_deal is None:
            # idempotency row survived a deal that somehow didn't; fall through and write again
            return None
        player = await self._players.get_by_id(cached_deal.player_id)
        nick_display = player.nick_display if player is not None else str(cached_deal.player_id)
        return TransactionRegistrationResult(
            deal=cached_deal, nick_display=nick_display, discord_bound=False, replayed=True
        )
