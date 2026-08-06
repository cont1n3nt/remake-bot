"""Tests for `stalbot.presentation.cogs.tickets.card.render_ticket_card` (PLAN.md §11.5)."""

from datetime import UTC, datetime

from stalbot.application.dto.ticket_session import TicketSession
from stalbot.domain.enums import DeliveryMethod, TicketKind, TicketStatus
from stalbot.presentation.cogs.tickets.card import SCREENSHOT_FILENAME, render_ticket_card
from stalbot.presentation.embeds.factory import EmbedFactory


def _session(**overrides: object) -> TicketSession:
    now = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)
    defaults: dict[str, object] = {
        "channel_id": 111,
        "kind": TicketKind.SELL_ITEMS,
        "author_id": 222,
        "status": TicketStatus.AWAITING_TOOL,
        "delivery_method": None,
        "game_nick": None,
        "referrer_nick": None,
        "referrer_discord_id": None,
        "deadline": None,
        "screenshot_url": None,
        "screenshot_message_id": None,
        "summary_message_id": None,
        "panel_message_id": None,
        "ocr_status": "disabled",
        "ocr_analysis_id": None,
        "idempotency_key": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return TicketSession(**defaults)  # type: ignore[arg-type]


def test_bare_session_shows_only_the_player_and_creation_time() -> None:
    embed = render_ticket_card(_session(), EmbedFactory())

    description = embed.description or ""
    assert "<@222>" in description
    assert "Игровой ник" not in description
    assert "Способ" not in description
    assert "Пригласил" not in description
    assert "01.08.2026 00:45" in description  # 21:45 UTC == 00:45 GMT+3 the next day


def test_title_matches_the_ticket_kind() -> None:
    embed = render_ticket_card(_session(kind=TicketKind.SELL_BOOSTS), EmbedFactory())

    assert embed.title == "🎫 Заявка на продажу бустов"


def test_filled_fields_are_shown() -> None:
    embed = render_ticket_card(
        _session(
            game_nick="Scaryyyyy",
            delivery_method=DeliveryMethod.MAIL,
            referrer_nick="OtherNick",
            referrer_discord_id=999,
        ),
        EmbedFactory(),
    )

    description = embed.description or ""
    assert "Scaryyyyy" in description
    assert "📬 Почта" in description
    assert "OtherNick (<@999>)" in description


def test_referrer_without_resolved_discord_id_shows_only_the_nick() -> None:
    embed = render_ticket_card(_session(referrer_nick="OtherNick"), EmbedFactory())

    referrer_line = next(
        line for line in (embed.description or "").splitlines() if "Пригласил" in line
    )
    assert referrer_line == "🤝 Пригласил: OtherNick"


def test_no_image_when_no_screenshot_yet() -> None:
    embed = render_ticket_card(_session(), EmbedFactory())

    assert embed.image.url is None


def test_image_points_at_the_reattached_file_once_a_screenshot_exists() -> None:
    embed = render_ticket_card(_session(screenshot_message_id=555), EmbedFactory())

    assert embed.image.url == f"attachment://{SCREENSHOT_FILENAME}"
