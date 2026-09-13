"""Tests for `infrastructure.cache.repositories.shop.ShopRepository` (п.2, migration 0012)."""

from datetime import UTC, datetime, timedelta

import aiosqlite

from stalbot.domain.entities.player import Player
from stalbot.domain.entities.shop import PlayerEffect, ShopCategory, ShopItem, ShopPurchase
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.shop import ShopRepository

_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def _player(connection: aiosqlite.Connection, nick: str = "scaryyyyy") -> int:
    players = PlayersRepository(connection)
    player: Player = await players.get_or_create(NormalizedNick(nick), nick, now=_NOW)
    assert player.id is not None
    return player.id


async def _category(shop: ShopRepository, key: str = "small") -> str:
    await shop.upsert_category(
        ShopCategory(key=key, name="🟢 Мелкие товары", sort_order=0), now=_NOW
    )
    return key


def _item(**overrides: object) -> ShopItem:
    defaults: dict[str, object] = {
        "id": None,
        "category_key": "small",
        "name": "Купон «Разовый Сейл»",
        "name_norm": "купон «разовый сейл»",
        "description": "Скидка 1% на следующую сделку.",
        "price_coins": 15,
        "effect_kind": "discount_percent",
        "effect_value": "1",
        "uses": 1,
    }
    defaults.update(overrides)
    return ShopItem(**defaults)  # type: ignore[arg-type]


# -- categories -------------------------------------------------------------


async def test_upsert_category_inserts_then_updates(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    await shop.upsert_category(ShopCategory(key="small", name="Мелочь"), now=_NOW)
    await shop.upsert_category(ShopCategory(key="small", name="Мелкие товары"), now=_NOW)

    stored = await shop.get_category("small")
    assert stored is not None
    assert stored.name == "Мелкие товары"
    assert len(await shop.categories()) == 1


async def test_inactive_categories_are_hidden_by_default(
    connection: aiosqlite.Connection,
) -> None:
    shop = ShopRepository(connection)
    await shop.upsert_category(ShopCategory(key="small", name="Мелочь", active=False), now=_NOW)

    assert await shop.categories() == []
    assert len(await shop.categories(include_inactive=True)) == 1


# -- items ------------------------------------------------------------------


async def test_insert_and_read_back_an_item(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    await _category(shop)

    item_id = await shop.insert_item(_item(), now=_NOW)

    stored = await shop.get_item(item_id)
    assert stored is not None
    assert stored.price_coins == 15
    assert stored.uses == 1
    assert stored.is_available


async def test_a_hidden_item_is_off_the_shelf_but_still_readable(
    connection: aiosqlite.Connection,
) -> None:
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(active=False), now=_NOW)

    assert await shop.items() == []
    assert len(await shop.items(include_hidden=True)) == 1
    assert await shop.get_item(item_id) is not None


async def test_a_soft_deleted_item_keeps_its_purchases_readable(
    connection: aiosqlite.Connection,
) -> None:
    """`shop_purchases` points at it with ON DELETE RESTRICT — it must never hard-delete."""
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(), now=_NOW)
    player_id = await _player(connection)
    await shop.insert_purchase(
        ShopPurchase(id=None, player_id=player_id, shop_item_id=item_id, price_coins=15), now=_NOW
    )

    await shop.soft_delete_item(item_id, now=_NOW)

    assert await shop.items() == []
    assert len(await shop.purchases_for_player(player_id)) == 1


async def test_items_filter_by_category(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    await _category(shop, "small")
    await _category(shop, "elite")
    await shop.insert_item(_item(category_key="small"), now=_NOW)
    await shop.insert_item(
        _item(category_key="elite", name="Франшиза", name_norm="франшиза"), now=_NOW
    )

    assert len(await shop.items(category_key="small")) == 1


async def test_stock_never_goes_below_zero(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(stock=1), now=_NOW)

    await shop.decrement_stock(item_id, now=_NOW)
    await shop.decrement_stock(item_id, now=_NOW)

    stored = await shop.get_item(item_id)
    assert stored is not None
    assert stored.stock == 0
    assert not stored.is_available


async def test_unlimited_stock_is_left_alone(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(stock=None), now=_NOW)

    await shop.decrement_stock(item_id, now=_NOW)

    stored = await shop.get_item(item_id)
    assert stored is not None
    assert stored.stock is None


async def test_refund_puts_stock_back(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(stock=3), now=_NOW)

    await shop.decrement_stock(item_id, now=_NOW)
    await shop.increment_stock(item_id, now=_NOW)

    stored = await shop.get_item(item_id)
    assert stored is not None
    assert stored.stock == 3


# -- purchases --------------------------------------------------------------


async def test_the_idempotency_key_finds_a_repeat_click(
    connection: aiosqlite.Connection,
) -> None:
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(), now=_NOW)
    player_id = await _player(connection)
    await shop.insert_purchase(
        ShopPurchase(
            id=None,
            player_id=player_id,
            shop_item_id=item_id,
            price_coins=15,
            idempotency_key="interaction-1",
        ),
        now=_NOW,
    )

    found = await shop.find_purchase_by_key("interaction-1")

    assert found is not None
    assert found.price_coins == 15


async def test_a_refunded_purchase_stops_counting_against_the_limit(
    connection: aiosqlite.Connection,
) -> None:
    """A refund means it never happened — including for «можно купить один раз»."""
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(per_player_limit=1), now=_NOW)
    player_id = await _player(connection)
    purchase_id = await shop.insert_purchase(
        ShopPurchase(id=None, player_id=player_id, shop_item_id=item_id, price_coins=15), now=_NOW
    )

    assert await shop.count_purchases(player_id, item_id) == 1
    await shop.set_purchase_status(purchase_id, "refunded", now=_NOW, refunded_by=42)
    assert await shop.count_purchases(player_id, item_id) == 0


async def test_a_refund_records_who_did_it(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(), now=_NOW)
    player_id = await _player(connection)
    purchase_id = await shop.insert_purchase(
        ShopPurchase(id=None, player_id=player_id, shop_item_id=item_id, price_coins=15), now=_NOW
    )

    await shop.set_purchase_status(purchase_id, "refunded", now=_NOW, refunded_by=42)

    stored = await shop.get_purchase(purchase_id)
    assert stored is not None
    assert stored.refunded_by == 42
    assert stored.refunded_at is not None


# -- effects ----------------------------------------------------------------


async def test_a_live_effect_comes_back(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    player_id = await _player(connection)
    await shop.insert_effect(
        PlayerEffect(id=None, player_id=player_id, effect_kind="discount_percent"), now=_NOW
    )

    live = await shop.live_effects(player_id, now=_NOW)

    assert len(live) == 1


async def test_an_expired_effect_does_not(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    player_id = await _player(connection)
    await shop.insert_effect(
        PlayerEffect(
            id=None,
            player_id=player_id,
            effect_kind="discount_percent",
            expires_at=_NOW - timedelta(hours=1),
        ),
        now=_NOW,
    )

    assert await shop.live_effects(player_id, now=_NOW) == []


async def test_an_effect_expiring_later_still_counts(connection: aiosqlite.Connection) -> None:
    shop = ShopRepository(connection)
    player_id = await _player(connection)
    await shop.insert_effect(
        PlayerEffect(
            id=None,
            player_id=player_id,
            effect_kind="markup_percent",
            expires_at=_NOW + timedelta(days=7),
        ),
        now=_NOW,
    )

    assert len(await shop.live_effects(player_id, now=_NOW)) == 1


async def test_consuming_the_last_use_marks_the_effect_spent(
    connection: aiosqlite.Connection,
) -> None:
    shop = ShopRepository(connection)
    player_id = await _player(connection)
    effect_id = await shop.insert_effect(
        PlayerEffect(id=None, player_id=player_id, effect_kind="markup_percent", uses_left=2),
        now=_NOW,
    )

    await shop.consume_effect(effect_id, now=_NOW)
    assert len(await shop.live_effects(player_id, now=_NOW)) == 1

    await shop.consume_effect(effect_id, now=_NOW)
    assert await shop.live_effects(player_id, now=_NOW) == []


async def test_an_unlimited_use_effect_is_not_consumed_by_uses(
    connection: aiosqlite.Connection,
) -> None:
    shop = ShopRepository(connection)
    player_id = await _player(connection)
    effect_id = await shop.insert_effect(
        PlayerEffect(id=None, player_id=player_id, effect_kind="queue_skip", uses_left=None),
        now=_NOW,
    )

    await shop.consume_effect(effect_id, now=_NOW)

    assert len(await shop.live_effects(player_id, now=_NOW)) == 1


async def test_expiring_an_effect_outright_removes_it(
    connection: aiosqlite.Connection,
) -> None:
    shop = ShopRepository(connection)
    player_id = await _player(connection)
    effect_id = await shop.insert_effect(
        PlayerEffect(id=None, player_id=player_id, effect_kind="queue_skip"), now=_NOW
    )

    await shop.expire_effect(effect_id, now=_NOW)

    assert await shop.live_effects(player_id, now=_NOW) == []


async def test_effects_for_purchase_is_what_a_refund_undoes(
    connection: aiosqlite.Connection,
) -> None:
    shop = ShopRepository(connection)
    await _category(shop)
    item_id = await shop.insert_item(_item(), now=_NOW)
    player_id = await _player(connection)
    purchase_id = await shop.insert_purchase(
        ShopPurchase(id=None, player_id=player_id, shop_item_id=item_id, price_coins=15), now=_NOW
    )
    await shop.insert_effect(
        PlayerEffect(
            id=None,
            player_id=player_id,
            purchase_id=purchase_id,
            effect_kind="discount_percent",
        ),
        now=_NOW,
    )

    assert len(await shop.effects_for_purchase(purchase_id)) == 1
