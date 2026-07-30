"""Регрессия на пункт 6: тег создателя вне эмбеда.

В ревизии e4619670 (bot/cogs/tickets.py, до разбиения на пакет) карточка
публиковалась так:

    await interaction.channel.send(content=interaction.user.mention, embed=embed)

Из-за `content=` над эмбедом висело отдельное упоминание, а второй такой же
вызов после получения скриншота отправлял ещё одну копию карточки. Тест
фиксирует, что публикация идёт без `content` и с подавленными упоминаниями.
"""
import asyncio
import types

import discord
import pytest

from bot.cogs.tickets import views_delivery
from bot.cogs.tickets.views_delivery import SaleModal, BoostOrderModal
from bot.cogs.tickets.storage import form_store


class RecordingChannel:
    def __init__(self):
        self.sends: list[dict] = []

    async def send(self, *args, **kwargs):
        self.sends.append(kwargs)
        return types.SimpleNamespace(id=999 + len(self.sends), attachments=[])

    async def fetch_message(self, message_id):
        if any(message_id == 999 + i + 1 for i in range(len(self.sends))):
            return types.SimpleNamespace(id=message_id, attachments=[])
        raise discord.NotFound(types.SimpleNamespace(status=404, reason="x"), "not found")


class RecordingFollowup:
    def __init__(self):
        self.sends: list[tuple] = []

    async def send(self, *args, **kwargs):
        self.sends.append((args, kwargs))


class FakeRepo:
    def get_all_items(self):
        return []


class FakeAudit:
    async def log(self, *args, **kwargs):
        return None


def make_interaction(channel):
    user = types.SimpleNamespace(
        id=705382500238884874,
        display_name="Scaryyyyy",
        mention="<@705382500238884874>",
        display_avatar=types.SimpleNamespace(url="https://example.invalid/a.png"),
        __str__=lambda self: "scaryyyyy",
    )
    return types.SimpleNamespace(
        user=user,
        guild=None,
        channel=channel,
        channel_id=4242,
        followup=RecordingFollowup(),
        client=types.SimpleNamespace(repo=FakeRepo(), audit_logger=FakeAudit()),
    )


@pytest.fixture
def no_disk_writes(monkeypatch):
    """Мета заявок живёт в памяти — как на диске, но без файлов."""
    saved: dict[int, tuple[int, dict]] = {}

    async def save_meta(channel_id, message_id, user_id, data):
        saved[channel_id] = (message_id, {"user_id": user_id, "data": data})

    async def noop(*args, **kwargs):
        return None

    def load_by_channel(channel_id):
        return saved.get(channel_id)

    monkeypatch.setattr(views_delivery, "_save_request_meta", save_meta)
    monkeypatch.setattr(views_delivery, "_save_deal_report", noop)
    monkeypatch.setattr(views_delivery, "_load_request_meta_by_channel", load_by_channel)
    return saved


def publish(category, monkeypatch):
    channel = RecordingChannel()
    interaction = make_interaction(channel)
    form_store.set(interaction.user.id, "text_data", {"game_nick": "scary"})
    form_store.set(interaction.user.id, "delivery_method", "Почта")
    form_store.set(interaction.user.id, "category", category)
    form_store.set(interaction.user.id, "selected_boosts", [])
    form_store.set(interaction.user.id, "total_price", 0.0)

    modal = BoostOrderModal(category) if "Заказ" in category else SaleModal(category)
    asyncio.run(modal._publish(interaction))
    form_store.clear(interaction.user.id)
    return channel, interaction


def test_request_card_is_sent_without_content(no_disk_writes, monkeypatch):
    channel, _ = publish("Продажа предметов", monkeypatch)
    assert len(channel.sends) == 1
    kwargs = channel.sends[0]
    assert "content" not in kwargs, "тег создателя снова уходит текстом над эмбедом"
    assert kwargs["embed"] is not None


def test_request_card_suppresses_mentions(no_disk_writes, monkeypatch):
    channel, _ = publish("Продажа предметов", monkeypatch)
    allowed = channel.sends[0]["allowed_mentions"]
    assert isinstance(allowed, discord.AllowedMentions)
    assert allowed.users is False and allowed.everyone is False and allowed.roles is False


def test_creator_mention_lives_only_inside_the_embed(no_disk_writes, monkeypatch):
    channel, interaction = publish("Продажа предметов", monkeypatch)
    embed = channel.sends[0]["embed"]
    creator = next(f for f in embed.fields if f.name == "👤 Создатель")
    assert creator.value == interaction.user.mention
    # И больше нигде: ни в content, ни отдельным сообщением в канал.
    assert len(channel.sends) == 1


def test_repeated_submit_does_not_publish_a_second_card(no_disk_writes):
    """Пункт 8: повторный сабмит формы в том же тикете не должен давать вторую
    карточку — именно так и появлялись дубли заявок на продажу."""
    channel = RecordingChannel()
    interaction = make_interaction(channel)
    form_store.set(interaction.user.id, "text_data", {"game_nick": "scary"})
    form_store.set(interaction.user.id, "category", "Продажа предметов")

    modal = SaleModal("Продажа предметов")

    async def submit_twice():
        await modal._publish(interaction)
        await modal._publish(interaction)

    asyncio.run(submit_twice())
    form_store.clear(interaction.user.id)
    assert len(channel.sends) == 1


def test_concurrent_submit_does_not_publish_a_second_card(no_disk_writes):
    channel = RecordingChannel()
    interaction = make_interaction(channel)
    form_store.set(interaction.user.id, "text_data", {"game_nick": "scary"})
    form_store.set(interaction.user.id, "category", "Продажа предметов")

    modal = SaleModal("Продажа предметов")

    async def race():
        await asyncio.gather(modal._publish(interaction), modal._publish(interaction))

    asyncio.run(race())
    form_store.clear(interaction.user.id)
    assert len(channel.sends) == 1


def test_card_is_republished_if_it_was_deleted(no_disk_writes):
    """Если карточку удалили из канала — новая заявка должна публиковаться."""
    channel = RecordingChannel()
    interaction = make_interaction(channel)
    form_store.set(interaction.user.id, "text_data", {"game_nick": "scary"})
    form_store.set(interaction.user.id, "category", "Продажа предметов")

    modal = SaleModal("Продажа предметов")
    asyncio.run(modal._publish(interaction))
    channel.sends.clear()  # имитируем удаление карточки: fetch_message теперь падает

    asyncio.run(modal._publish(interaction))
    form_store.clear(interaction.user.id)
    assert len(channel.sends) == 1


def test_boost_order_gets_no_screenshot_prompt(no_disk_writes, monkeypatch):
    """Пункт 7: в заказе бустов сообщения про скриншот быть не должно."""
    _channel, interaction = publish("Заказ бустов", monkeypatch)
    texts = [args[0] for args, _kwargs in interaction.followup.sends if args]
    assert not any("скриншот" in t.lower() for t in texts)


def test_sale_gets_screenshot_requirements(no_disk_writes, monkeypatch):
    _channel, interaction = publish("Продажа предметов", monkeypatch)
    texts = [args[0] for args, _kwargs in interaction.followup.sends if args]
    assert any("скриншот" in t.lower() for t in texts)
