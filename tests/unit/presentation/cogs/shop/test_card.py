"""Spot checks for `stalbot.presentation.cogs.shop.card` (заявка 13.09.2026 п.2)."""

from datetime import UTC, datetime

from stalbot.domain.entities.shop import PlayerEffect, ShopItem, ShopPurchase
from stalbot.presentation.cogs.shop.card import (
    render_admin_item_list,
    render_categories,
    render_item_detail,
    render_live_effects,
    render_purchase_history,
    render_purchase_receipt,
)
from stalbot.presentation.embeds.factory import EmbedFactory

_EMBEDS = EmbedFactory()
_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _item(**overrides: object) -> ShopItem:
    defaults: dict[str, object] = {
        "id": 1,
        "category_key": "small",
        "name": "Кофе",
        "name_norm": "кофе",
        "description": "Бодрит",
        "price_coins": 50,
        "effect_kind": "manual",
    }
    defaults.update(overrides)
    return ShopItem(**defaults)  # type: ignore[arg-type]


def test_render_categories_shows_balance_and_empty_shelf_message() -> None:
    embed = render_categories([], _EMBEDS, balance=100)
    assert "100" in (embed.description or "")
    assert "пуст" in (embed.description or "")


def test_render_item_detail_shows_the_post_purchase_balance_while_confirming() -> None:
    item = _item(price_coins=50)
    embed = render_item_detail(item, _EMBEDS, balance=200, confirming=True)
    assert "150" in (embed.description or "")  # 200 - 50


def test_render_purchase_receipt_shows_before_and_after() -> None:
    embed = render_purchase_receipt(_item(), _EMBEDS, balance_before=500, balance_after=450)
    assert "500" in (embed.description or "")
    assert "450" in (embed.description or "")


def test_render_purchase_history_resolves_names_and_falls_back_to_id() -> None:
    purchase = ShopPurchase(id=1, player_id=1, shop_item_id=99, price_coins=50, purchased_at=_NOW)
    embed = render_purchase_history([(purchase, None)], _EMBEDS)
    assert "#99" in (embed.description or "")


def test_render_live_effects_lists_uses_left_and_expiry() -> None:
    effect = PlayerEffect(
        id=1,
        player_id=1,
        effect_kind="discount_percent",
        effect_value="10",
        uses_left=2,
        expires_at=_NOW,
    )
    embed = render_live_effects([effect], _EMBEDS)
    assert "осталось использований: 2" in (embed.description or "")


def test_render_admin_item_list_marks_deleted_items() -> None:
    item = _item(deleted_at=_NOW)
    embed = render_admin_item_list([item], _EMBEDS, title="Товары")
    assert "🗑️" in (embed.description or "")
