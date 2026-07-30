"""Тесты карточки заявки и вспомогательных функций тикетов.

Покрывают пункты 3, 4, 5, 11, 13, 14: порядок полей, формат бустов, блок
«Почта» с ником магазина, сумма заявки и разбор Discord ID.
"""
import types

import pytest

from bot.config.constants import SHOP_MAIL_NICK
from bot.cogs.tickets.embeds import (
    MANUAL_CALC_TEXT, build_request_card_embed, build_audit_details, format_boost_lines,
)
from bot.cogs.tickets.views_edit import _extract_discord_id


class FakeRepo:
    def __init__(self, items=None):
        self._items = items or [
            {"name": "Чесночный суп", "category": "boost", "price_sell": 6700, "emoji": ""},
            {"name": "Солянка", "category": "boost", "price_sell": 4000, "emoji": ""},
        ]

    def get_all_items(self):
        return self._items


def fake_author(name="Scaryyyyy"):
    return types.SimpleNamespace(
        display_name=name,
        mention=f"<@705382500238884874>",
        display_avatar=types.SimpleNamespace(url="https://example.invalid/a.png"),
    )


def build(**overrides):
    kwargs = dict(
        author=fake_author(),
        guild=None,
        repo=FakeRepo(),
        text_data={"game_nick": "scary"},
        delivery="Трейд",
        boosts=[],
        total_price=0.0,
        category="Продажа предметов",
    )
    kwargs.update(overrides)
    return build_request_card_embed(**kwargs)


def field_names(embed):
    return [f.name for f in embed.fields]


def field_value(embed, name):
    return next(f.value for f in embed.fields if f.name == name)


# --- Пункт 4: порядок полей ----------------------------------------------

def test_creator_is_the_first_field_and_on_its_own_row():
    embed = build()
    assert embed.fields[0].name == "👤 Создатель"
    assert embed.fields[0].inline is False


def test_field_order_nick_then_method_then_deadline():
    embed = build(
        category="Заказ бустов",
        text_data={"game_nick": "scary", "deadline": "30.08 18:30"},
    )
    names = field_names(embed)
    assert names.index("👤 Создатель") < names.index("🎮 Ник в игре")
    assert names.index("🎮 Ник в игре") < names.index("💳 Способ")
    assert names.index("💳 Способ") < names.index("⏰ До даты и времени")


# --- Пункт 3: бусты «эмодзи Название | Количество» ------------------------

def test_boost_lines_carry_quantity():
    boosts = [{"name": "Солянка", "quantity": 2}, {"name": "Чесночный суп", "quantity": 1}]
    lines = format_boost_lines(boosts, {}, None)
    assert lines == ["Солянка | 2", "Чесночный суп | 1"]


def test_boosts_field_uses_quantity_format():
    embed = build(
        category="Заказ бустов",
        boosts=[{"name": "Солянка", "quantity": 3}],
        total_price=12000,
    )
    assert field_value(embed, "📦 Выбранные бусты") == "Солянка | 3"
    assert field_value(embed, "💰 Общая стоимость") == "12 000 ₽"


# --- Пункт 5: блок «Почта» внизу и с ником магазина -----------------------

def test_mail_block_is_last_and_uses_shop_nick_not_player_nick():
    embed = build(delivery="Почта", text_data={"game_nick": "scary"})
    assert embed.fields[-1].name == "📮 Почта"
    value = embed.fields[-1].value
    assert f"Ник: {SHOP_MAIL_NICK}" in value
    assert "scary" not in value, "подставлен игровой ник заявителя вместо ника магазина"


def test_mail_block_wording_depends_on_category():
    sale = build(delivery="Почта", category="Продажа предметов")
    order = build(delivery="Почта", category="Заказ бустов")
    assert "ресурсы" in field_value(sale, "📮 Почта")
    assert "деньги" in field_value(order, "📮 Почта")


def test_method_field_has_no_parenthetical_hint():
    embed = build(delivery="Почта")
    assert field_value(embed, "💳 Способ") == "📮 Почта"


def test_no_mail_block_for_trade():
    embed = build(delivery="Трейд")
    assert "📮 Почта" not in field_names(embed)


# --- Пункт 11: сумма заявки ----------------------------------------------

def test_sale_without_ocr_total_says_manual():
    embed = build(total_price=0.0)
    assert field_value(embed, "💰 Сумма заявки") == MANUAL_CALC_TEXT


def test_sale_with_ocr_total_is_formatted_with_separators():
    embed = build(total_price=1000000)
    assert field_value(embed, "💰 Сумма заявки") == "1 000 000 ₽"


def test_order_without_boosts_has_no_amount_field():
    embed = build(category="Заказ бустов", boosts=[], total_price=0.0)
    assert "💰 Сумма заявки" not in field_names(embed)


# --- Пункт 14: детали лога --------------------------------------------

def test_audit_details_keep_boost_quantity():
    details = build_audit_details(
        FakeRepo(), None, {"game_nick": "scary"}, "Трейд",
        [{"name": "Солянка", "quantity": 2}], 8000, "Заказ бустов",
    )
    assert "Солянка | 2" in details["Бусты"]
    assert details["Общая стоимость"] == "8 000 ₽"


def test_audit_details_for_sale_report_manual_amount():
    details = build_audit_details(
        FakeRepo(), None, {"game_nick": "scary"}, "Почта", [], 0.0, "Продажа предметов",
    )
    assert details["Сумма заявки"] == MANUAL_CALC_TEXT


# --- Пункт 13: разбор Discord ID -----------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("705382500238884874", "705382500238884874"),
    ("<@705382500238884874>", "705382500238884874"),
    ("<@!705382500238884874>", "705382500238884874"),
    ("  705382500238884874  ", "705382500238884874"),
    ("scary", None),
    ("", None),
    ("123", None),
])
def test_extract_discord_id(raw, expected):
    assert _extract_discord_id(raw) == expected
