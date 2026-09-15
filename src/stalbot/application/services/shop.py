"""Buying, refunding and administering the Coins shop (заявка 13.09.2026 п.2).

Backs `/shop` and the `/shop_item` admin group (Discord layer built
separately). This module owns the two-sided consistency a purchase needs:
the player's Coins move by exactly the price, the effect they bought is
attached (or, for an immediate grant, applied) in the same call, and a
refund undoes both sides — never one without the other.

Every purchase runs through the same order in `_buy_locked`: the item is
still on sale and correctly configured (`_require_available`,
`_require_configured`), the player has not hit its per-player limit and
can afford it, the price is paid (`coin_ledger`), and the effect is
attached (`player_effects`) — or, for `xp_grant`/`coins_grant`, resolved
and paid out on the spot, since those are not something a ticket ever
"spends" later. Every check that can fail runs *before* the first write,
so a refusal never leaves a half-charged purchase behind.
"""

import asyncio
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final, Literal

from stalbot.application.ports.clock import Clock
from stalbot.domain.entities.coin_ledger import CoinLedgerEntry
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.shop import PlayerEffect, ShopCategory, ShopItem, ShopPurchase
from stalbot.domain.entities.xp_ledger import XpLedgerEntry
from stalbot.domain.errors import (
    DuplicateShopItemError,
    InsufficientCoinsError,
    ItemNotFoundError,
    PlayerNotLinkedError,
    ShopItemMisconfiguredError,
    ShopItemUnavailableError,
    ShopPurchaseAlreadyRefundedError,
    ShopPurchaseLimitReachedError,
    ShopPurchaseNotFoundError,
)
from stalbot.domain.shop.effects import (
    EffectKind,
    parse_amount_range,
    parse_both_percent,
    parse_percent,
)
from stalbot.infrastructure.cache.repositories.coin_ledger import CoinLedgerRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.infrastructure.cache.repositories.shop import ShopRepository
from stalbot.infrastructure.cache.repositories.xp_ledger import XpLedgerRepository

#: Kinds resolved and paid out at purchase time rather than attached as a
#: live perk — the grant *is* the purchase, there is nothing left to spend
#: later the way a discount or a queue-skip is spent.
_IMMEDIATE_GRANT_KINDS: Final = frozenset({EffectKind.XP_GRANT, EffectKind.COINS_GRANT})

PercentSide = Literal["discount", "markup"]


class _Unset:
    """Sentinel distinguishing "not passed" from an explicit `None`."""

    def __repr__(self) -> str:
        return "<unset>"


_UNSET: Final = _Unset()


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    """What `buy()` did."""

    purchase: ShopPurchase
    item: ShopItem
    effect: PlayerEffect
    new_balance: int
    replayed: bool = False
    """`True` if this was a repeat call with the same idempotency key — a
    double-click or a retried interaction, not a second purchase."""


@dataclass(frozen=True, slots=True)
class RefundResult:
    """What `refund()` did."""

    purchase: ShopPurchase
    item: ShopItem
    refunded_coins: int
    new_balance: int
    reversed_effects: tuple[PlayerEffect, ...]


class ShopService:
    """Browsing, buying, refunding, and the admin CRUD behind `/shop_item`."""

    def __init__(
        self,
        shop: ShopRepository,
        players: PlayersRepository,
        progression: ProgressionRepository,
        coin_ledger: CoinLedgerRepository,
        xp_ledger: XpLedgerRepository,
        *,
        clock: Clock,
        random_range: Callable[[int, int], int] = random.randint,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            shop: Cache repository for categories/items/purchases/effects.
            players: Resolves a Discord id to the player row it bought as.
            progression: Recomputes Coins/XP after every ledger write.
            coin_ledger: Debits a purchase, credits a refund or a Coins grant.
            xp_ledger: Credits an XP grant, or claws one back on refund.
            clock: Time source, tz-aware `GMT3`.
            random_range: Picks the resolved amount for a ranged grant
                («Кейс "Слепая Удача"», 50-150 Coins). Injectable so tests
                don't depend on real randomness.
        """
        self._shop = shop
        self._players = players
        self._progression = progression
        self._coin_ledger = coin_ledger
        self._xp_ledger = xp_ledger
        self._clock = clock
        self._random_range = random_range
        #: Serializes buy() the same way TransactionService serializes
        #: register() (CLUSTER-1): a double-clicked "Купить" must not both
        #: pass the balance/limit checks before either has recorded its
        #: purchase.
        self._lock = asyncio.Lock()

    # --- browsing ------------------------------------------------------

    async def categories(self, *, include_inactive: bool = False) -> Sequence[ShopCategory]:
        """Every category, in display order.

        Args:
            include_inactive: Whether to include hidden categories (the
                admin editor needs them; the shelf must not show them).
        """
        return await self._shop.categories(include_inactive=include_inactive)

    async def get_category(self, key: str) -> ShopCategory | None:
        """Look up one category.

        Args:
            key: `shop_categories.key`.
        """
        return await self._shop.get_category(key)

    async def items(
        self, *, category_key: str | None = None, include_hidden: bool = False
    ) -> Sequence[ShopItem]:
        """Items on sale, optionally narrowed to one category.

        Args:
            category_key: Restrict to one category, or `None` for all.
            include_hidden: Include inactive/soft-deleted items — the admin
                editor's own list, never the shelf.
        """
        return await self._shop.items(category_key=category_key, include_hidden=include_hidden)

    async def get_item(self, item_id: int) -> ShopItem | None:
        """Look up one item, soft-deleted ones included.

        Args:
            item_id: `shop_items.id`.
        """
        return await self._shop.get_item(item_id)

    async def balance(self, discord_id: int) -> int:
        """A player's current Coins balance.

        Args:
            discord_id: The Discord account asking.

        Raises:
            PlayerNotLinkedError: This account has no linked game nick.
        """
        player = await self._require_player(discord_id)
        assert player.id is not None  # noqa: S101 - _require_player guarantees a persisted id
        record = await self._progression.get(player.id)
        return record.coins if record is not None else 0

    async def my_purchases(
        self, discord_id: int, *, limit: int | None = None
    ) -> Sequence[ShopPurchase]:
        """One player's purchase history, newest first.

        Args:
            discord_id: The Discord account asking.
            limit: Cap the result, or `None` for all of it.

        Raises:
            PlayerNotLinkedError: This account has no linked game nick.
        """
        player = await self._require_player(discord_id)
        assert player.id is not None  # noqa: S101 - _require_player guarantees a persisted id
        return await self._shop.purchases_for_player(player.id, limit=limit)

    async def my_live_effects(self, discord_id: int) -> Sequence[PlayerEffect]:
        """Every perk still applying to a player right now.

        Args:
            discord_id: The Discord account asking.

        Raises:
            PlayerNotLinkedError: This account has no linked game nick.
        """
        player = await self._require_player(discord_id)
        assert player.id is not None  # noqa: S101 - _require_player guarantees a persisted id
        return await self._shop.live_effects(player.id, now=self._clock.now())

    # --- buying ----------------------------------------------------------

    async def buy(self, discord_id: int, item_id: int, *, idempotency_key: str) -> PurchaseResult:
        """Buy one item, paying its price and attaching (or granting) its effect.

        Idempotent on *idempotency_key* (the confirming click's interaction
        id) — a double-click or a retried interaction returns the original
        purchase instead of charging twice.

        Args:
            discord_id: The buyer's Discord account.
            item_id: `shop_items.id`.
            idempotency_key: Unique per confirming click.

        Raises:
            PlayerNotLinkedError: The buyer has no linked game nick.
            ItemNotFoundError: No such item.
            ShopItemUnavailableError: The item or its category is off, or
                out of stock.
            ShopPurchaseLimitReachedError: The buyer already owns as many
                as `per_player_limit` allows.
            InsufficientCoinsError: Not enough Coins.
            ShopItemMisconfiguredError: The item's `effect_value` cannot be
                read for what its `effect_kind` needs.
        """
        async with self._lock:
            return await self._buy_locked(discord_id, item_id, idempotency_key)

    async def _buy_locked(
        self, discord_id: int, item_id: int, idempotency_key: str
    ) -> PurchaseResult:
        replayed = await self._shop.find_purchase_by_key(idempotency_key)
        if replayed is not None:
            item = await self._shop.get_item(replayed.shop_item_id)
            assert item is not None  # noqa: S101 - RESTRICT guarantees the item still exists
            assert replayed.id is not None  # noqa: S101 - a fetched purchase always has an id
            effects = await self._shop.effects_for_purchase(replayed.id)
            balance = await self.balance(discord_id)
            return PurchaseResult(
                purchase=replayed,
                item=item,
                effect=effects[0] if effects else _empty_effect(replayed),
                new_balance=balance,
                replayed=True,
            )

        player = await self._require_player(discord_id)
        assert player.id is not None  # noqa: S101 - a resolved player always has a persisted id
        item = await self._shop.get_item(item_id)
        if item is None:
            raise ItemNotFoundError(str(item_id))
        await self._require_available(item)
        self._require_configured(item)

        if item.per_player_limit is not None:
            owned = await self._shop.count_purchases(player.id, item_id)
            if owned >= item.per_player_limit:
                raise ShopPurchaseLimitReachedError(
                    f"«{item.name}» можно купить не больше {item.per_player_limit} раз — "
                    "лимит уже исчерпан."
                )

        record = await self._progression.get(player.id)
        current_coins = record.coins if record is not None else 0
        if current_coins < item.price_coins:
            raise InsufficientCoinsError(
                f"Не хватает Coins: нужно {item.price_coins}, на балансе {current_coins}."  # noqa: RUF001
            )

        now = self._clock.now()
        await self._coin_ledger.add(
            CoinLedgerEntry(
                id=None,
                player_id=player.id,
                delta=-item.price_coins,
                reason=f"shop_buy:{item.id}",
                created_by=discord_id,
                created_at=now,
            )
        )
        await self._shop.decrement_stock(item_id, now=now)
        purchase_id = await self._shop.insert_purchase(
            ShopPurchase(
                id=None,
                player_id=player.id,
                shop_item_id=item_id,
                price_coins=item.price_coins,
                idempotency_key=idempotency_key,
            ),
            now=now,
        )
        effect = await self._attach_effect(player.id, purchase_id, item, now=now)
        await self._progression.recompute([player.id], now=now)

        purchase = await self._shop.get_purchase(purchase_id)
        assert purchase is not None  # noqa: S101 - just inserted
        new_balance = await self.balance(discord_id)
        return PurchaseResult(purchase=purchase, item=item, effect=effect, new_balance=new_balance)

    async def _attach_effect(
        self, player_id: int, purchase_id: int, item: ShopItem, *, now: datetime
    ) -> PlayerEffect:
        """Attach the item's effect, or — for a grant — resolve and pay it out."""
        if item.effect_kind in _IMMEDIATE_GRANT_KINDS:
            return await self._grant_immediately(player_id, purchase_id, item, now=now)

        expires_at = now + timedelta(days=item.duration_days) if item.duration_days else None
        effect_id = await self._shop.insert_effect(
            PlayerEffect(
                id=None,
                player_id=player_id,
                purchase_id=purchase_id,
                effect_kind=item.effect_kind,
                effect_value=item.effect_value,
                uses_left=item.uses,
                expires_at=expires_at,
            ),
            now=now,
        )
        return PlayerEffect(
            id=effect_id,
            player_id=player_id,
            purchase_id=purchase_id,
            effect_kind=item.effect_kind,
            effect_value=item.effect_value,
            uses_left=item.uses,
            expires_at=expires_at,
            created_at=now,
        )

    async def _grant_immediately(
        self, player_id: int, purchase_id: int, item: ShopItem, *, now: datetime
    ) -> PlayerEffect:
        amount_range = parse_amount_range(item.effect_value)
        if amount_range is None:
            raise ShopItemMisconfiguredError(
                f"«{item.name}»: значение эффекта «{item.effect_value}» не читается как "
                "число или диапазон — покупка отменена, ничего не списано."
            )
        low, high = amount_range
        amount = self._random_range(low, high) if low != high else low

        if item.effect_kind == EffectKind.XP_GRANT:
            await self._xp_ledger.add(
                XpLedgerEntry(
                    id=None,
                    player_id=player_id,
                    delta=amount,
                    reason=f"shop_grant:{item.id}",
                    created_by=None,
                    created_at=now,
                )
            )
        else:
            await self._coin_ledger.add(
                CoinLedgerEntry(
                    id=None,
                    player_id=player_id,
                    delta=amount,
                    reason=f"shop_grant:{item.id}",
                    created_by=None,
                    created_at=now,
                )
            )

        effect_id = await self._shop.insert_effect(
            PlayerEffect(
                id=None,
                player_id=player_id,
                purchase_id=purchase_id,
                effect_kind=item.effect_kind,
                effect_value=str(amount),
                note=f"Выдано: {amount}",
            ),
            now=now,
        )
        # Not a live perk — the grant already happened. Marking it consumed
        # keeps it out of `live_effects()` while `effects_for_purchase()`
        # still shows it for history and for a refund to claw back.
        await self._shop.expire_effect(effect_id, now=now)
        return PlayerEffect(
            id=effect_id,
            player_id=player_id,
            purchase_id=purchase_id,
            effect_kind=item.effect_kind,
            effect_value=str(amount),
            note=f"Выдано: {amount}",
            created_at=now,
            consumed_at=now,
        )

    async def _require_available(self, item: ShopItem) -> None:
        if not item.is_available:
            raise ShopItemUnavailableError(f"«{item.name}» сейчас недоступен для покупки.")
        category = await self._shop.get_category(item.category_key)
        if category is None or not category.active:
            raise ShopItemUnavailableError(f"«{item.name}» сейчас недоступен для покупки.")

    def _require_configured(self, item: ShopItem) -> None:
        """Catch a misconfigured item *before* anything is charged or written.

        `_grant_immediately` re-checks this at grant time too (defense in
        depth if it is ever called outside `buy()`), but by then the price
        would already be debited and the purchase row already inserted —
        this call has to happen first, or a misconfigured item charges the
        player for a grant that then fails to resolve.
        """
        if (
            item.effect_kind in _IMMEDIATE_GRANT_KINDS
            and parse_amount_range(item.effect_value) is None
        ):
            raise ShopItemMisconfiguredError(
                f"«{item.name}»: значение эффекта «{item.effect_value}» не читается как "
                "число или диапазон — покупка отменена, ничего не списано."
            )

    async def _require_player(self, discord_id: int) -> Player:
        player = await self._players.get_by_discord_id(discord_id)
        if player is None or player.id is None:
            raise PlayerNotLinkedError(
                "Ваш Discord-аккаунт не привязан к игровому нику — магазин недоступен, "
                "пока нет ни одной сделки или привязки."
            )
        return player

    # --- refunding (admin) -----------------------------------------------

    async def refund(self, purchase_id: int, *, admin_id: int) -> RefundResult:
        """Undo a purchase: credit back its price, restore stock, kill its effects.

        Args:
            purchase_id: `shop_purchases.id`.
            admin_id: Discord id of the admin refunding it.

        Raises:
            ShopPurchaseNotFoundError: No such purchase.
            ShopPurchaseAlreadyRefundedError: Already refunded once.
        """
        purchase = await self._shop.get_purchase(purchase_id)
        if purchase is None:
            raise ShopPurchaseNotFoundError(f"Покупка #{purchase_id} не найдена.")
        if purchase.status == "refunded":
            raise ShopPurchaseAlreadyRefundedError(
                f"Покупка #{purchase_id} уже была возвращена — повторный возврат "
                "списал бы Coins дважды."
            )

        item = await self._shop.get_item(purchase.shop_item_id)
        assert item is not None  # noqa: S101 - RESTRICT guarantees the item still exists
        now = self._clock.now()

        await self._coin_ledger.add(
            CoinLedgerEntry(
                id=None,
                player_id=purchase.player_id,
                delta=purchase.price_coins,
                reason=f"shop_refund:{purchase.id}",
                created_by=admin_id,
                created_at=now,
            )
        )
        await self._shop.increment_stock(purchase.shop_item_id, now=now)

        reversed_effects: list[PlayerEffect] = []
        for effect in await self._shop.effects_for_purchase(purchase_id):
            reversed_effects.append(await self._reverse_effect(effect, now=now))

        await self._shop.set_purchase_status(purchase_id, "refunded", now=now, refunded_by=admin_id)
        await self._progression.recompute([purchase.player_id], now=now)

        record = await self._progression.get(purchase.player_id)
        return RefundResult(
            purchase=replace(purchase, status="refunded", refunded_at=now, refunded_by=admin_id),
            item=item,
            refunded_coins=purchase.price_coins,
            new_balance=record.coins if record is not None else 0,
            reversed_effects=tuple(reversed_effects),
        )

    async def _reverse_effect(self, effect: PlayerEffect, *, now: datetime) -> PlayerEffect:
        assert effect.id is not None  # noqa: S101 - a fetched effect always has an id
        if effect.consumed_at is None:
            # A still-live perk (discount, queue skip, ...) — simply killed.
            await self._shop.expire_effect(effect.id, now=now)
            return replace(effect, consumed_at=now)

        # Already consumed — for a grant, that means already paid out, and
        # the payout has to be clawed back or the refund is a free top-up.
        if effect.effect_kind in _IMMEDIATE_GRANT_KINDS and effect.effect_value is not None:
            amount = int(effect.effect_value)
            entry_reason = f"shop_refund_clawback:{effect.purchase_id}"
            if effect.effect_kind == EffectKind.XP_GRANT:
                await self._xp_ledger.add(
                    XpLedgerEntry(
                        id=None,
                        player_id=effect.player_id,
                        delta=-amount,
                        reason=entry_reason,
                        created_by=None,
                        created_at=now,
                    )
                )
            else:
                await self._coin_ledger.add(
                    CoinLedgerEntry(
                        id=None,
                        player_id=effect.player_id,
                        delta=-amount,
                        reason=entry_reason,
                        created_by=None,
                        created_at=now,
                    )
                )
        return effect

    # --- percent effects (queue for /recipe-style ticket integration) ----

    async def apply_percent_effects(
        self, discord_id: int, side: PercentSide, *, consume: bool = True
    ) -> Decimal:
        """Sum every live percent effect on one side, for a ticket confirm.

        A duration-limited effect (`expires_at` set, `uses_left` `None`)
        keeps applying to every ticket until it expires on its own — it is
        not spent by being used. A uses-limited one (`uses_left` set — an
        item good for a fixed number of deals) is spent here instead: one
        use is consumed per matching effect that actually contributed.

        Args:
            discord_id: The ticket author.
            side: `"discount"` (заказ бустов) or `"markup"` (скупка).
            consume: Whether to actually spend a use of each uses-limited
                effect that contributed. `False` previews the number
                without spending anything — the caller's own confirm flow
                has to know the discount *before* it knows whether this
                particular call is the one that will actually record the
                deal (a staggered double-confirm can have two callers reach
                this point, and only one of them ends up writing — see
                `TicketsCog._on_amount_submitted`). Preview first to price
                the deal, then call again with `consume=True` only once the
                write is confirmed to be this call's own.

        Returns:
            The combined percent — `0` if the player holds none, or is not
            even linked (a ticket already requires a bound nick by the time
            this runs, so that case is treated as "no bonus" rather than
            raised).
        """
        player = await self._players.get_by_discord_id(discord_id)
        if player is None or player.id is None:
            return Decimal(0)

        now = self._clock.now()
        total = Decimal(0)
        for effect in await self._shop.live_effects(player.id, now=now):
            contributed = _percent_contribution(effect, side)
            if contributed is None:
                continue
            total += contributed
            if consume and effect.uses_left is not None:
                assert effect.id is not None  # noqa: S101 - a fetched effect always has an id
                await self._shop.consume_effect(effect.id, now=now)
        return total

    async def apply_xp_multiplier(self, discord_id: int, *, consume: bool = True) -> Decimal:
        """Sum every live `xp_multiplier` effect, for a deal confirm.

        Same use-vs-duration rule, and the same preview/consume split, as
        `apply_percent_effects`. `grant_deal_xp_bonus` is the usual entry
        point — it calls this and turns the percent into an actual credit.

        Args:
            discord_id: The player whose deal is being confirmed.
            consume: Whether to spend a use of each uses-limited effect
                that contributed.

        Returns:
            The combined percent — `0` if none apply.
        """
        player = await self._players.get_by_discord_id(discord_id)
        if player is None or player.id is None:
            return Decimal(0)

        now = self._clock.now()
        total = Decimal(0)
        for effect in await self._shop.live_effects(player.id, now=now):
            if effect.effect_kind != EffectKind.XP_MULTIPLIER:
                continue
            percent = parse_percent(effect.effect_value)
            if percent is None:
                continue
            total += percent
            if consume and effect.uses_left is not None:
                assert effect.id is not None  # noqa: S101 - a fetched effect always has an id
                await self._shop.consume_effect(effect.id, now=now)
        return total

    async def grant_deal_xp_bonus(self, discord_id: int, deal_xp: int) -> int:
        """Credit the XP an active `xp_multiplier` effect adds to one just-recorded deal.

        Called once, after a deal has actually been written (never on a
        replayed/losing confirm — see `apply_percent_effects`'s docstring
        for why that distinction matters). `deal_reward()` already decided
        `deal_xp` from the deal's own turnover formula; this adds the
        multiplier on top as a separate `xp_ledger` credit rather than
        reaching back into that computation, which is what lets `/cost`-
        style recomputation stay ignorant of shop effects entirely.

        Args:
            discord_id: The player whose deal was just recorded.
            deal_xp: The deal's own XP reward (`Deal.xp` — before any
                shop bonus), the base the percent applies to.

        Returns:
            The bonus XP actually credited — `0` if no multiplier applies.
        """
        percent = await self.apply_xp_multiplier(discord_id)
        if percent <= 0 or deal_xp <= 0:
            return 0
        bonus = int((Decimal(deal_xp) * percent / 100).to_integral_value())
        if bonus <= 0:
            return 0

        player = await self._players.get_by_discord_id(discord_id)
        if player is None or player.id is None:
            return 0
        now = self._clock.now()
        await self._xp_ledger.add(
            XpLedgerEntry(
                id=None,
                player_id=player.id,
                delta=bonus,
                reason="shop_xp_multiplier",
                created_by=None,
                created_at=now,
            )
        )
        await self._progression.recompute([player.id], now=now)
        return bonus

    # --- queue skip & referrals --------------------------------------------

    async def consume_queue_skip(self, discord_id: int) -> bool:
        """Spend one live `queue_skip` effect for *discord_id*, if they have one.

        Called once a ticket's real author is definitively known (the form
        submit, not `_infer_author_id`'s best-effort channel-open guess) —
        an Экспресс-талон is for whichever ticket its buyer opens next, not
        one chosen in advance.

        Args:
            discord_id: The ticket's author.

        Returns:
            Whether an effect was actually spent — the caller uses this to
            decide whether the ticket channel gets renamed.
        """
        player = await self._players.get_by_discord_id(discord_id)
        if player is None or player.id is None:
            return False
        now = self._clock.now()
        for effect in await self._shop.live_effects(player.id, now=now):
            if effect.effect_kind == EffectKind.QUEUE_SKIP:
                assert effect.id is not None  # noqa: S101 - a fetched effect always has an id
                await self._shop.consume_effect(effect.id, now=now)
                return True
        return False

    async def grant_referral_welcome_bonus(
        self, new_player_id: int, referrer_player_id: int
    ) -> int:
        """Credit a freshly-referred player +1 Coin if the referrer holds a live «Личная Франшиза».

        Called once, from `TransactionService`, at the exact moment a
        player's referrer is bound for the first time — `referrer_player_id`
        is only ever set once per player and never rewritten, so this can
        never double-grant for the same referral. The referrer's own
        ongoing reward is not a separate mechanic: it is whatever
        `domain.progression.calculator`'s existing referral-turnover math
        already credits them for having a referred player at all — «Личная
        Франшиза» only makes the referrer nameable via a purchase, and adds
        this one-time welcome grant on top.

        Args:
            new_player_id: The just-referred player receiving the bonus.
            referrer_player_id: Whose live `promo_code` effect to check.

        Returns:
            `1` if a bonus was granted, `0` if the referrer holds no live
            «Личная Франшиза» effect.
        """
        now = self._clock.now()
        effects = await self._shop.live_effects(referrer_player_id, now=now)
        if not any(effect.effect_kind == EffectKind.PROMO_CODE for effect in effects):
            return 0
        await self._coin_ledger.add(
            CoinLedgerEntry(
                id=None,
                player_id=new_player_id,
                delta=1,
                reason="shop_promo_welcome",
                created_by=None,
                created_at=now,
            )
        )
        await self._progression.recompute([new_player_id], now=now)
        return 1

    # --- admin CRUD --------------------------------------------------------

    async def upsert_category(
        self,
        key: str,
        name: str,
        *,
        description: str | None = None,
        sort_order: int = 0,
        active: bool = True,
    ) -> ShopCategory:
        """Create or update a category (`/shop_item category`).

        Args:
            key: Stable identifier — never shown to a player.
            name: Display name, e.g. "🟢 Мелкие товары".
            description: Shown above the item list.
            sort_order: Lower sorts first.
            active: Whether it (and everything in it) is on the shelf.
        """
        category = ShopCategory(
            key=key, name=name, description=description, sort_order=sort_order, active=active
        )
        await self._shop.upsert_category(category, now=self._clock.now())
        stored = await self._shop.get_category(key)
        assert stored is not None  # noqa: S101 - just upserted
        return stored

    async def add_item(
        self,
        category_key: str,
        name: str,
        description: str,
        price_coins: int,
        effect_kind: str,
        *,
        effect_value: str | None = None,
        duration_days: int | None = None,
        uses: int | None = None,
        stock: int | None = None,
        per_player_limit: int | None = None,
        emoji: str | None = None,
        sort_order: int = 0,
    ) -> ShopItem:
        """Add a new item to the assortment (`/shop_item add`).

        Raises:
            ItemNotFoundError: No such category.
            DuplicateShopItemError: An item with this name already exists.
        """
        if await self._shop.get_category(category_key) is None:
            raise ItemNotFoundError(category_key)
        name_norm = normalize_item_name(name)
        if await self._shop.find_item_by_name(name_norm) is not None:
            raise DuplicateShopItemError(f"«{name}» уже есть в магазине.")

        now = self._clock.now()
        item_id = await self._shop.insert_item(
            ShopItem(
                id=None,
                category_key=category_key,
                name=name,
                name_norm=name_norm,
                description=description,
                price_coins=price_coins,
                effect_kind=effect_kind,
                effect_value=effect_value,
                duration_days=duration_days,
                uses=uses,
                stock=stock,
                per_player_limit=per_player_limit,
                emoji=emoji,
                sort_order=sort_order,
            ),
            now=now,
        )
        stored = await self._shop.get_item(item_id)
        assert stored is not None  # noqa: S101 - just inserted
        return stored

    async def update_item(
        self,
        item_id: int,
        *,
        category_key: str | None = None,
        name: str | None = None,
        description: str | None = None,
        price_coins: int | None = None,
        effect_kind: str | None = None,
        effect_value: str | _Unset | None = _UNSET,
        duration_days: int | _Unset | None = _UNSET,
        uses: int | _Unset | None = _UNSET,
        stock: int | _Unset | None = _UNSET,
        per_player_limit: int | _Unset | None = _UNSET,
        emoji: str | _Unset | None = _UNSET,
        sort_order: int | None = None,
        active: bool | None = None,
    ) -> ShopItem:
        """Change some of an item's fields; anything left out keeps its value.

        `effect_value`/`duration_days`/`uses`/`stock`/`per_player_limit`/
        `emoji` default to a private sentinel rather than `None`, because
        `None` is itself a meaningful value for every one of them (clear
        the field) that has to be distinguishable from "not passed".

        Raises:
            ItemNotFoundError: No such item, or no such category.
            DuplicateShopItemError: Renamed to a name another item already has.
        """
        current = await self._shop.get_item(item_id)
        if current is None:
            raise ItemNotFoundError(str(item_id))
        if category_key is not None and await self._shop.get_category(category_key) is None:
            raise ItemNotFoundError(category_key)
        if name is not None:
            collision = await self._shop.find_item_by_name(normalize_item_name(name))
            if collision is not None and collision.id != item_id:
                raise DuplicateShopItemError(f"«{name}» уже есть в магазине.")

        # Reassigning each sentinel-defaulted local (rather than an inline
        # `x if x is _UNSET else ...` ternary) is what lets mypy narrow
        # `str | _Unset | None` down to the field's real `str | None` type —
        # it does not narrow a class-instance `is` check inside a ternary.
        resolved_effect_value = current.effect_value
        if not isinstance(effect_value, _Unset):
            resolved_effect_value = effect_value
        resolved_duration_days = current.duration_days
        if not isinstance(duration_days, _Unset):
            resolved_duration_days = duration_days
        resolved_uses = current.uses
        if not isinstance(uses, _Unset):
            resolved_uses = uses
        resolved_stock = current.stock
        if not isinstance(stock, _Unset):
            resolved_stock = stock
        resolved_per_player_limit = current.per_player_limit
        if not isinstance(per_player_limit, _Unset):
            resolved_per_player_limit = per_player_limit
        resolved_emoji = current.emoji
        if not isinstance(emoji, _Unset):
            resolved_emoji = emoji

        updated = replace(
            current,
            category_key=category_key if category_key is not None else current.category_key,
            name=name if name is not None else current.name,
            name_norm=normalize_item_name(name) if name is not None else current.name_norm,
            description=description if description is not None else current.description,
            price_coins=price_coins if price_coins is not None else current.price_coins,
            effect_kind=effect_kind if effect_kind is not None else current.effect_kind,
            effect_value=resolved_effect_value,
            duration_days=resolved_duration_days,
            uses=resolved_uses,
            stock=resolved_stock,
            per_player_limit=resolved_per_player_limit,
            emoji=resolved_emoji,
            sort_order=sort_order if sort_order is not None else current.sort_order,
            active=active if active is not None else current.active,
        )
        await self._shop.update_item(updated, now=self._clock.now())
        stored = await self._shop.get_item(item_id)
        assert stored is not None  # noqa: S101 - just updated
        return stored

    async def delete_item(self, item_id: int) -> ShopItem:
        """Soft-delete an item — past purchases and effects stay valid.

        Args:
            item_id: `shop_items.id`.

        Raises:
            ItemNotFoundError: No such item.
        """
        item = await self._shop.get_item(item_id)
        if item is None:
            raise ItemNotFoundError(str(item_id))
        now = self._clock.now()
        await self._shop.soft_delete_item(item_id, now=now)
        stored = await self._shop.get_item(item_id)
        assert stored is not None  # noqa: S101 - soft delete never removes the row
        return stored

    async def recent_purchases(self, *, limit: int = 25) -> Sequence[ShopPurchase]:
        """The latest purchases across everyone — the admin's refund picker.

        Args:
            limit: How many to return.
        """
        return await self._shop.recent_purchases(limit=limit)


def _percent_contribution(effect: PlayerEffect, side: PercentSide) -> Decimal | None:
    """What one live effect contributes to *side*, or `None` if it does not apply."""
    if effect.effect_kind == EffectKind.BOTH_PERCENT:
        both = parse_both_percent(effect.effect_value)
        if both is None:
            return None
        return both.discount if side == "discount" else both.markup
    wanted_kind = EffectKind.DISCOUNT_PERCENT if side == "discount" else EffectKind.MARKUP_PERCENT
    if effect.effect_kind != wanted_kind:
        return None
    return parse_percent(effect.effect_value)


def _empty_effect(purchase: ShopPurchase) -> PlayerEffect:
    """A placeholder for a replayed purchase whose effect row was not found.

    Should not happen in practice — every purchase attaches exactly one
    effect — but a display fallback is cheaper than an assertion failure
    on a replay path, which by definition runs on a *second* look at data
    already committed by the first call.
    """
    return PlayerEffect(
        id=None, player_id=purchase.player_id, purchase_id=purchase.id, effect_kind="unknown"
    )
