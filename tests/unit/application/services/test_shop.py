"""Tests for `application.services.shop.ShopService` (заявка 13.09.2026 п.2).

Real SQLite repositories throughout, same reasoning as `test_recipes.py`:
this service's whole job is keeping two ledgers and a perk table
consistent with each other, and a mocked repository would prove none of
that consistency.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiosqlite
import pytest

from stalbot.application.services.shop import (
    PurchaseResult,
    RefundResult,
    ShopService,
)
from stalbot.domain.entities.coin_ledger import CoinLedgerEntry
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
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.coin_ledger import CoinLedgerRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.infrastructure.cache.repositories.shop import ShopRepository
from stalbot.infrastructure.cache.repositories.xp_ledger import XpLedgerRepository

_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
_DISCORD_ID = 111


class _MutableClock:
    """A `Clock` whose `now()` can be advanced mid-test (expiry scenarios)."""

    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


def _service(
    connection: aiosqlite.Connection,
    *,
    clock: _MutableClock | None = None,
    random_range: object = None,
) -> tuple[ShopService, PlayersRepository, ProgressionRepository]:
    players = PlayersRepository(connection)
    progression = ProgressionRepository(connection)
    kwargs: dict[str, object] = {"clock": clock or _MutableClock(_NOW)}
    if random_range is not None:
        kwargs["random_range"] = random_range
    service = ShopService(
        ShopRepository(connection),
        players,
        progression,
        CoinLedgerRepository(connection),
        XpLedgerRepository(connection),
        **kwargs,  # type: ignore[arg-type]
    )
    return service, players, progression


async def _player_with_coins(
    connection: aiosqlite.Connection,
    players: PlayersRepository,
    progression: ProgressionRepository,
    *,
    discord_id: int = _DISCORD_ID,
    coins: int = 100,
    nick: str = "scaryyyyy",
) -> int:
    coin_ledger = CoinLedgerRepository(connection)
    player = await players.get_or_create(NormalizedNick(nick), nick, now=_NOW)
    assert player.id is not None
    await players.bind_discord(NormalizedNick(nick), discord_id, force=True, now=_NOW)
    if coins:
        await coin_ledger.add(
            CoinLedgerEntry(
                id=None,
                player_id=player.id,
                delta=coins,
                reason="seed",
                created_by=None,
                created_at=_NOW,
            )
        )
    await progression.recompute([player.id], now=_NOW)
    return player.id


async def _category(service: ShopService, key: str = "small") -> str:
    await service.upsert_category(key, "🟢 Мелкие товары")
    return key


# -- browsing -----------------------------------------------------------


async def test_balance_of_an_unlinked_account_raises(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)

    with pytest.raises(PlayerNotLinkedError):
        await service.balance(999)


async def test_balance_reflects_the_seeded_ledger(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=250)

    assert await service.balance(_DISCORD_ID) == 250


async def test_categories_and_items_round_trip(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)
    await _category(service)
    await service.add_item("small", "Купон", "Скидка 1%.", 15, "discount_percent", effect_value="1")

    categories = await service.categories()
    items = await service.items(category_key="small")

    assert [c.key for c in categories] == ["small"]
    assert len(items) == 1
    assert items[0].name == "Купон"


# -- buying: the happy paths ---------------------------------------------


async def test_buy_debits_the_price_and_attaches_a_duration_effect(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small",
        "Карта Закупщика",
        "Скидка 2% на 7 дней.",
        65,
        "discount_percent",
        effect_value="2",
        duration_days=7,
    )
    assert item.id is not None

    result = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert isinstance(result, PurchaseResult)
    assert result.new_balance == 35
    assert result.purchase.status == "active"
    assert result.effect.effect_value == "2"
    assert result.effect.expires_at == _NOW + timedelta(days=7)
    assert result.effect.uses_left is None
    assert not result.replayed


async def test_buy_attaches_a_uses_limited_effect(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small",
        "Тайный Контракт",
        "Наценка 2% на 3 сделки.",
        75,
        "markup_percent",
        effect_value="2",
        uses=3,
    )
    assert item.id is not None

    result = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert result.effect.uses_left == 3
    assert result.effect.expires_at is None


async def test_buy_decrements_limited_stock(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "Кейс", "Разовый.", 10, "manual", stock=5)
    assert item.id is not None

    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    updated = await service.get_item(item.id)
    assert updated is not None
    assert updated.stock == 4


async def test_a_fixed_xp_grant_lands_exactly(connection: aiosqlite.Connection) -> None:
    """base_xp is 0 at zero turnover, so the boost formula never engages — deterministic."""
    service, players, progression = _service(connection)
    player_id = await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Сухой паёк", "+75 XP.", 35, "xp_grant", effect_value="75"
    )
    assert item.id is not None

    result = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert result.effect.effect_value == "75"
    assert result.effect.consumed_at is not None
    record = await progression.get(player_id)
    assert record is not None
    assert record.xp == 75


async def test_a_ranged_coins_grant_uses_the_injected_random_source(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection, random_range=lambda low, high: 112)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Кейс «Слепая Удача»", "50-150 Coins.", 90, "coins_grant", effect_value="50-150"
    )
    assert item.id is not None

    result = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert result.effect.effect_value == "112"
    assert result.new_balance == 100 - 90 + 112


async def test_a_fixed_range_grant_skips_the_random_source(
    connection: aiosqlite.Connection,
) -> None:
    calls: list[tuple[int, int]] = []

    def _record_and_answer(low: int, high: int) -> int:
        calls.append((low, high))
        return 999

    service, players, progression = _service(connection, random_range=_record_and_answer)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Сухой паёк", "+75 XP.", 35, "xp_grant", effect_value="75"
    )
    assert item.id is not None

    result = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert result.effect.effect_value == "75"
    assert calls == []


# -- buying: refusals -----------------------------------------------------


async def test_buy_refuses_an_unlinked_account(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual")
    assert item.id is not None

    with pytest.raises(PlayerNotLinkedError):
        await service.buy(999, item.id, idempotency_key="click-1")


async def test_buy_refuses_an_unknown_item(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression)

    with pytest.raises(ItemNotFoundError):
        await service.buy(_DISCORD_ID, 999, idempotency_key="click-1")


async def test_buy_refuses_an_inactive_item(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual")
    assert item.id is not None
    await service.update_item(item.id, active=False)

    with pytest.raises(ShopItemUnavailableError):
        await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")


async def test_buy_refuses_a_deleted_item(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual")
    assert item.id is not None
    await service.delete_item(item.id)

    with pytest.raises(ShopItemUnavailableError):
        await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")


async def test_buy_refuses_an_out_of_stock_item(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual", stock=0)
    assert item.id is not None

    with pytest.raises(ShopItemUnavailableError):
        await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")


async def test_buy_refuses_an_item_whose_category_is_hidden(
    connection: aiosqlite.Connection,
) -> None:
    """An item can stay `active=True` while its whole category is turned off."""
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual")
    assert item.id is not None
    await service.upsert_category("small", "🟢 Мелкие товары", active=False)

    with pytest.raises(ShopItemUnavailableError):
        await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")


async def test_buy_refuses_insufficient_coins_and_charges_nothing(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=5)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 15, "manual")
    assert item.id is not None

    with pytest.raises(InsufficientCoinsError):
        await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert await service.balance(_DISCORD_ID) == 5
    assert await service.my_purchases(_DISCORD_ID) == []


async def test_buy_refuses_past_the_per_player_limit(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual", per_player_limit=1)
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    with pytest.raises(ShopPurchaseLimitReachedError):
        await service.buy(_DISCORD_ID, item.id, idempotency_key="click-2")


async def test_a_refunded_purchase_frees_up_the_limit_again(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual", per_player_limit=1)
    assert item.id is not None
    first = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")
    await service.refund(first.purchase.id, admin_id=1)  # type: ignore[arg-type]

    second = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-2")

    assert second.purchase.id != first.purchase.id


async def test_a_misconfigured_grant_charges_nothing(connection: aiosqlite.Connection) -> None:
    """The bug this guards: validating *before* any write, not after the price is gone."""
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Битый кейс", "?", 10, "coins_grant", effect_value="не число"
    )
    assert item.id is not None

    with pytest.raises(ShopItemMisconfiguredError):
        await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert await service.balance(_DISCORD_ID) == 100
    assert await service.my_purchases(_DISCORD_ID) == []


# -- idempotency ------------------------------------------------------------


async def test_a_repeated_click_is_not_charged_twice(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 15, "manual")
    assert item.id is not None

    first = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")
    second = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert not first.replayed
    assert second.replayed
    assert second.purchase.id == first.purchase.id
    assert await service.balance(_DISCORD_ID) == 85


# -- refunds ------------------------------------------------------------


async def test_refund_credits_back_the_price_and_kills_a_live_effect(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Карта", "Скидка.", 40, "discount_percent", effect_value="2", duration_days=7
    )
    assert item.id is not None
    bought = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    result = await service.refund(bought.purchase.id, admin_id=999)  # type: ignore[arg-type]

    assert isinstance(result, RefundResult)
    assert result.refunded_coins == 40
    assert result.new_balance == 100
    assert result.purchase.status == "refunded"
    assert result.purchase.refunded_by == 999
    assert result.reversed_effects[0].consumed_at is not None
    assert await service.apply_percent_effects(_DISCORD_ID, "discount") == 0


async def test_refund_restores_limited_stock(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual", stock=3)
    assert item.id is not None
    bought = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    await service.refund(bought.purchase.id, admin_id=1)  # type: ignore[arg-type]

    updated = await service.get_item(item.id)
    assert updated is not None
    assert updated.stock == 3


async def test_refund_claws_back_a_coins_grant_leaving_balance_unchanged(
    connection: aiosqlite.Connection,
) -> None:
    """Buy-then-refund of a grant must be a net no-op — no free top-up."""
    service, players, progression = _service(connection, random_range=lambda lo, hi: 112)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Кейс", "50-150.", 15, "coins_grant", effect_value="50-150"
    )
    assert item.id is not None
    bought = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")
    assert bought.new_balance == 100 - 15 + 112

    await service.refund(bought.purchase.id, admin_id=1)  # type: ignore[arg-type]

    assert await service.balance(_DISCORD_ID) == 100


async def test_refund_claws_back_an_xp_grant(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    player_id = await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Сухой паёк", "+75 XP.", 35, "xp_grant", effect_value="75"
    )
    assert item.id is not None
    bought = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    await service.refund(bought.purchase.id, admin_id=1)  # type: ignore[arg-type]

    record = await progression.get(player_id)
    assert record is not None
    assert record.xp == 0


async def test_refund_of_an_unknown_purchase_raises(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)

    with pytest.raises(ShopPurchaseNotFoundError):
        await service.refund(999, admin_id=1)


async def test_refunding_twice_raises(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual")
    assert item.id is not None
    bought = await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")
    await service.refund(bought.purchase.id, admin_id=1)  # type: ignore[arg-type]

    with pytest.raises(ShopPurchaseAlreadyRefundedError):
        await service.refund(bought.purchase.id, admin_id=1)  # type: ignore[arg-type]


# -- percent effects: the ticket-confirm integration point ----------------


async def test_apply_percent_effects_is_zero_with_nothing_bought(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression)

    assert await service.apply_percent_effects(_DISCORD_ID, "discount") == 0


async def test_a_discount_effect_never_contributes_to_markup(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Карта", "Скидка.", 40, "discount_percent", effect_value="2", duration_days=7
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert await service.apply_percent_effects(_DISCORD_ID, "discount") == Decimal(2)
    assert await service.apply_percent_effects(_DISCORD_ID, "markup") == 0


async def test_both_percent_contributes_to_both_sides(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=200)
    await _category(service)
    item = await service.add_item(
        "small",
        "Золотой Абонемент",
        "3% / 1.5%.",
        150,
        "both_percent",
        effect_value='{"discount": 3, "markup": 1.5}',
        duration_days=14,
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert await service.apply_percent_effects(_DISCORD_ID, "discount") == Decimal(3)
    assert await service.apply_percent_effects(_DISCORD_ID, "markup") == Decimal("1.5")


async def test_a_duration_effect_is_not_consumed_by_use(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Карта", "Скидка.", 40, "discount_percent", effect_value="2", duration_days=7
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    first = await service.apply_percent_effects(_DISCORD_ID, "discount")
    second = await service.apply_percent_effects(_DISCORD_ID, "discount")

    assert first == second == Decimal(2)


async def test_a_uses_limited_effect_is_spent_by_use(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Контракт", "Наценка.", 75, "markup_percent", effect_value="2", uses=2
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    first = await service.apply_percent_effects(_DISCORD_ID, "markup")
    second = await service.apply_percent_effects(_DISCORD_ID, "markup")
    third = await service.apply_percent_effects(_DISCORD_ID, "markup")

    assert (first, second, third) == (Decimal(2), Decimal(2), Decimal(0))


async def test_an_expired_effect_no_longer_contributes(connection: aiosqlite.Connection) -> None:
    clock = _MutableClock(_NOW)
    service, players, progression = _service(connection, clock=clock)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Карта", "Скидка.", 40, "discount_percent", effect_value="2", duration_days=7
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    clock.value = _NOW + timedelta(days=8)

    assert await service.apply_percent_effects(_DISCORD_ID, "discount") == 0


async def test_preview_does_not_spend_a_uses_limited_effect(
    connection: aiosqlite.Connection,
) -> None:
    """The split `TicketsCog` relies on: price the deal first, spend only once it is confirmed."""
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Тайный Контракт", "Наценка.", 75, "markup_percent", effect_value="2", uses=1
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    previewed_twice = (
        await service.apply_percent_effects(_DISCORD_ID, "markup", consume=False),
        await service.apply_percent_effects(_DISCORD_ID, "markup", consume=False),
    )
    consumed = await service.apply_percent_effects(_DISCORD_ID, "markup", consume=True)
    after_consuming = await service.apply_percent_effects(_DISCORD_ID, "markup", consume=False)

    assert previewed_twice == (Decimal(2), Decimal(2))
    assert consumed == Decimal(2)
    assert after_consuming == 0


async def test_grant_deal_xp_bonus_credits_the_percentage_of_the_deal(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    player_id = await _player_with_coins(connection, players, progression, coins=300)
    await _category(service)
    item = await service.add_item(
        "small", "Гильдия", "+50% XP.", 175, "xp_multiplier", effect_value="50", duration_days=7
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    bonus = await service.grant_deal_xp_bonus(_DISCORD_ID, 40)

    assert bonus == 20
    record = await progression.get(player_id)
    assert record is not None
    assert record.xp == 20


async def test_grant_deal_xp_bonus_is_zero_with_no_active_multiplier(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)

    assert await service.grant_deal_xp_bonus(_DISCORD_ID, 40) == 0


async def test_grant_deal_xp_bonus_spends_a_uses_limited_multiplier(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=300)
    await _category(service)
    item = await service.add_item(
        "small", "Гильдия", "+50% XP.", 175, "xp_multiplier", effect_value="50", uses=1
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    first = await service.grant_deal_xp_bonus(_DISCORD_ID, 40)
    second = await service.grant_deal_xp_bonus(_DISCORD_ID, 40)

    assert (first, second) == (20, 0)


async def test_apply_xp_multiplier_ignores_percent_effects(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=300)
    await _category(service)
    discount = await service.add_item(
        "small", "Карта", "Скидка.", 40, "discount_percent", effect_value="2", duration_days=7
    )
    multiplier = await service.add_item(
        "small", "Гильдия", "+50% XP.", 175, "xp_multiplier", effect_value="50", duration_days=7
    )
    assert discount.id is not None and multiplier.id is not None
    await service.buy(_DISCORD_ID, discount.id, idempotency_key="click-1")
    await service.buy(_DISCORD_ID, multiplier.id, idempotency_key="click-2")

    assert await service.apply_xp_multiplier(_DISCORD_ID) == Decimal(50)


# -- admin CRUD -----------------------------------------------------------


async def test_upsert_category_inserts_then_updates(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)
    await service.upsert_category("small", "Мелочь")

    await service.upsert_category("small", "Мелкие товары", sort_order=1)

    stored = await service.get_category("small")
    assert stored is not None
    assert stored.name == "Мелкие товары"
    assert stored.sort_order == 1


async def test_add_item_refuses_an_unknown_category(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)

    with pytest.raises(ItemNotFoundError):
        await service.add_item("ghost", "X", "Y", 10, "manual")


async def test_add_item_refuses_a_duplicate_name(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)
    await _category(service)
    await service.add_item("small", "Купон", "Y", 10, "manual")

    with pytest.raises(DuplicateShopItemError):
        await service.add_item("small", "Купон", "Другое описание.", 20, "manual")


async def test_update_item_leaves_unspecified_fields_alone(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _progression = _service(connection)
    await _category(service)
    item = await service.add_item(
        "small", "X", "Y", 10, "discount_percent", effect_value="1", stock=5
    )
    assert item.id is not None

    updated = await service.update_item(item.id, price_coins=20)

    assert updated.price_coins == 20
    assert updated.name == "X"
    assert updated.stock == 5
    assert updated.effect_value == "1"


async def test_update_item_can_explicitly_clear_an_optional_field(
    connection: aiosqlite.Connection,
) -> None:
    """`stock=None` must mean "make it unlimited", distinguishable from "leave it"."""
    service, _players, _progression = _service(connection)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual", stock=5)
    assert item.id is not None

    updated = await service.update_item(item.id, stock=None)

    assert updated.stock is None


async def test_update_item_refuses_renaming_onto_another_items_name(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _progression = _service(connection)
    await _category(service)
    await service.add_item("small", "Первый", "Y", 10, "manual")
    second = await service.add_item("small", "Второй", "Y", 10, "manual")
    assert second.id is not None

    with pytest.raises(DuplicateShopItemError):
        await service.update_item(second.id, name="Первый")


async def test_update_item_may_keep_its_own_name(connection: aiosqlite.Connection) -> None:
    """Re-passing the current name (e.g. alongside another field) must not self-collide."""
    service, _players, _progression = _service(connection)
    await _category(service)
    item = await service.add_item("small", "Купон", "Y", 10, "manual")
    assert item.id is not None

    updated = await service.update_item(item.id, name="Купон", price_coins=20)

    assert updated.price_coins == 20


async def test_update_item_refuses_moving_to_an_unknown_category(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _progression = _service(connection)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual")
    assert item.id is not None

    with pytest.raises(ItemNotFoundError):
        await service.update_item(item.id, category_key="ghost")


async def test_update_item_refuses_an_unknown_item(connection: aiosqlite.Connection) -> None:
    service, _players, _progression = _service(connection)

    with pytest.raises(ItemNotFoundError):
        await service.update_item(999, price_coins=1)


async def test_delete_item_is_soft_and_keeps_it_readable(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _progression = _service(connection)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual")
    assert item.id is not None

    await service.delete_item(item.id)

    assert await service.items(category_key="small") == []
    stored = await service.get_item(item.id)
    assert stored is not None
    assert stored.deleted_at is not None


async def test_recent_purchases_is_newest_first_and_respects_the_limit(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "X", "Y", 10, "manual", stock=None)
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-2")

    recent = await service.recent_purchases(limit=1)

    assert len(recent) == 1
    assert recent[0].idempotency_key == "click-2"


# -- queue skip & referrals (заявка 13.09.2026 п.2, 15.09.2026 decisions) ---


async def test_consume_queue_skip_spends_a_live_effect(connection: aiosqlite.Connection) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item("small", "Талон", "Без очереди.", 20, "queue_skip", uses=1)
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    first = await service.consume_queue_skip(_DISCORD_ID)
    second = await service.consume_queue_skip(_DISCORD_ID)

    assert (first, second) == (True, False)


async def test_consume_queue_skip_is_false_with_no_active_talon(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)

    assert await service.consume_queue_skip(_DISCORD_ID) is False


async def test_consume_queue_skip_is_false_for_an_unlinked_discord_id(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _progression = _service(connection)

    assert await service.consume_queue_skip(_DISCORD_ID) is False


async def test_consume_queue_skip_ignores_other_effect_kinds(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    await _player_with_coins(connection, players, progression, coins=100)
    await _category(service)
    item = await service.add_item(
        "small", "Скидка", "2%.", 20, "discount_percent", effect_value="2", duration_days=7
    )
    assert item.id is not None
    await service.buy(_DISCORD_ID, item.id, idempotency_key="click-1")

    assert await service.consume_queue_skip(_DISCORD_ID) is False


async def test_grant_referral_welcome_bonus_credits_the_new_player(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    referrer_id = await _player_with_coins(
        connection, players, progression, discord_id=222, nick="franchisor", coins=300
    )
    new_player_id = await _player_with_coins(
        connection, players, progression, discord_id=333, nick="newbie", coins=0
    )
    await _category(service)
    item = await service.add_item(
        "small", "Франшиза", "Промокод.", 200, "promo_code", duration_days=30
    )
    assert item.id is not None
    await service.buy(222, item.id, idempotency_key="click-1")

    bonus = await service.grant_referral_welcome_bonus(new_player_id, referrer_id)

    assert bonus == 1
    record = await progression.get(new_player_id)
    assert record is not None
    assert record.coins == 1


async def test_grant_referral_welcome_bonus_is_zero_without_a_live_franchise(
    connection: aiosqlite.Connection,
) -> None:
    service, players, progression = _service(connection)
    referrer_id = await _player_with_coins(
        connection, players, progression, discord_id=222, nick="franchisor", coins=0
    )
    new_player_id = await _player_with_coins(
        connection, players, progression, discord_id=333, nick="newbie", coins=0
    )

    bonus = await service.grant_referral_welcome_bonus(new_player_id, referrer_id)

    assert bonus == 0
    record = await progression.get(new_player_id)
    assert record is None or record.coins == 0
