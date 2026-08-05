"""Tests for `stalbot.presentation.embeds.factory` (PLAN.md §5.3, §5.4)."""

from datetime import datetime

import pytest

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.domain.clock import GMT3
from stalbot.domain.enums import TicketKind
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.embeds.palette import Color

NOW = datetime(2026, 7, 31, 21, 45, tzinfo=GMT3)


class _FakeClock:
    def now(self) -> datetime:
        return NOW


@pytest.fixture
def factory() -> EmbedFactory:
    return EmbedFactory(clock=_FakeClock())


def test_footer_format(factory: EmbedFactory) -> None:
    embed = factory.info("Заголовок")
    assert embed.footer.text == "Stalzone • 31.07.2026 21:45 (GMT+3)"


def test_author_uses_platform_name(factory: EmbedFactory) -> None:
    embed = factory.info("Заголовок")
    assert embed.author.name == "Stalzone"


@pytest.mark.parametrize(
    ("builder", "color"),
    [
        ("success", Color.SUCCESS),
        ("info", Color.INFO),
        ("warning", Color.WARNING),
        ("error", Color.ERROR),
    ],
)
def test_builder_colors(factory: EmbedFactory, builder: str, color: int) -> None:
    embed = getattr(factory, builder)("Заголовок", "Описание")
    assert embed.color is not None
    assert embed.color.value == color
    assert embed.title == "Заголовок"
    assert embed.description == "Описание"


def test_ticket_title_is_fixed_by_kind(factory: EmbedFactory) -> None:
    embed = factory.ticket(TicketKind.ORDER_BOOSTS)
    assert embed.title == "🎫 Заявка на заказ бустов"
    assert embed.color is not None
    assert embed.color.value == Color.TICKET


def test_ticket_title_override_keeps_the_ticket_color(factory: EmbedFactory) -> None:
    embed = factory.ticket(TicketKind.ORDER_BOOSTS, title="🧾 Редактор заказа")
    assert embed.title == "🧾 Редактор заказа"
    assert embed.color is not None
    assert embed.color.value == Color.TICKET


def test_title_is_truncated(factory: EmbedFactory) -> None:
    embed = factory.info("x" * 300)
    assert embed.title is not None
    assert len(embed.title) == 256
    assert embed.title.endswith("…")


def test_description_is_truncated(factory: EmbedFactory) -> None:
    embed = factory.info("Заголовок", "y" * 5000)
    assert embed.description is not None
    assert len(embed.description) == 4096
    assert embed.description.endswith("…")


def test_audit_embed_fields(factory: EmbedFactory) -> None:
    event = AuditEvent(
        user_id=123,
        user_display="@scary",
        channel_display="#ticket-0042",
        command="/add",
        arguments="тип=Покупка • ник=Scaryyyyy • сумма=299 900 ₽",
        result="Успешно",
        duration_seconds=0.84,
        trace_id="a1b2c3d4",
        occurred_at=NOW,
    )
    embed = factory.audit(event)

    assert embed.title == "🧾 Использование команды"
    assert embed.color is not None
    assert embed.color.value == Color.AUDIT
    assert embed.footer.text == "Stalzone • 31.07.2026 21:45 (GMT+3)"

    values = {field.name: field.value for field in embed.fields}
    assert values["👤 Пользователь"] == "@scary (ID: 123)"
    assert values["📍 Канал"] == "#ticket-0042"
    assert values["⌨️ Команда"] == "/add"
    assert values["⏱️ Длительность"] == "0.84 с"
    assert values["🔗 Trace"] == "a1b2c3d4"


def test_audit_empty_arguments_render_as_dash(factory: EmbedFactory) -> None:
    event = AuditEvent(
        user_id=1,
        user_display="@x",
        channel_display="#c",
        command="/profile",
        arguments="",
        result="Успешно",
        duration_seconds=0.1,
        trace_id="deadbeef",
        occurred_at=NOW,
    )
    embed = factory.audit(event)
    values = {field.name: field.value for field in embed.fields}
    assert values["📝 Аргументы"] == "—"


class TestEnforceLimits:
    def test_caps_field_count(self, factory: EmbedFactory) -> None:
        embed = factory.info("Заголовок")
        for i in range(30):
            embed.add_field(name=f"F{i}", value="v", inline=False)
        enforce_limits(embed)
        assert len(embed.fields) == 25

    def test_shrinks_total_length_under_budget(self, factory: EmbedFactory) -> None:
        embed = factory.info("Заголовок")
        for i in range(25):
            embed.add_field(name=f"F{i}", value="x" * 1024, inline=False)
        enforce_limits(embed)
        assert len(embed) <= 6000

    def test_clamps_field_value_to_1024_even_when_total_is_small(
        self, factory: EmbedFactory
    ) -> None:
        embed = factory.info("Заголовок")
        embed.add_field(name="F", value="x" * 1200, inline=False)
        enforce_limits(embed)
        assert embed.fields[0].value is not None
        assert len(embed.fields[0].value) <= 1024

    def test_clamps_field_name_to_256_even_when_total_is_small(
        self, factory: EmbedFactory
    ) -> None:
        embed = factory.info("Заголовок")
        embed.add_field(name="N" * 300, value="v", inline=False)
        enforce_limits(embed)
        assert embed.fields[0].name is not None
        assert len(embed.fields[0].name) <= 256

    def test_trim_loop_terminates_when_values_are_already_at_the_placeholder(
        self, factory: EmbedFactory
    ) -> None:
        embed = factory.info("Заголовок")
        for _ in range(25):
            embed.add_field(name="N" * 256, value="—", inline=False)
        # Must return promptly instead of looping forever: every field's
        # value is already the placeholder, so the trim loop cannot shrink
        # them any further and must give up rather than spin.
        enforce_limits(embed)
        assert all(field.value == "—" for field in embed.fields)
