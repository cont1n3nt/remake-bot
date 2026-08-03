"""Tests for `stalbot.config.ids` (PLAN.md §9.1, §11.1)."""

from stalbot.config.ids import (
    PARTNER_ROLE_ID,
    RANK_ROLE_IDS,
    REFERRAL_ROLE_IDS,
    TICKET_CATEGORIES,
    TICKET_TOOL_BOT_ID,
)
from stalbot.domain.enums import TicketKind


def test_ticket_categories_cover_every_ticket_kind() -> None:
    assert set(TICKET_CATEGORIES.values()) == set(TicketKind)


def test_ids_are_unique_within_each_mapping() -> None:
    assert len(set(TICKET_CATEGORIES)) == len(TICKET_CATEGORIES)
    assert len(set(RANK_ROLE_IDS.values())) == len(RANK_ROLE_IDS)
    assert len(set(REFERRAL_ROLE_IDS.values())) == len(REFERRAL_ROLE_IDS)


def test_rank_and_referral_ladders_have_five_tiers() -> None:
    assert len(RANK_ROLE_IDS) == 5
    assert len(REFERRAL_ROLE_IDS) == 5


def test_partner_role_is_distinct_from_referral_ladder() -> None:
    assert PARTNER_ROLE_ID not in REFERRAL_ROLE_IDS.values()


def test_ticket_tool_bot_id_is_not_confused_with_a_role_or_category() -> None:
    assert TICKET_TOOL_BOT_ID not in TICKET_CATEGORIES
    assert TICKET_TOOL_BOT_ID not in RANK_ROLE_IDS.values()
