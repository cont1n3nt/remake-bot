"""Tests for `domain.shop.effects` (заявка 13.09.2026 п.2).

The parsing rules matter more than they look: an `effect_value` the bot
cannot read must come back as `None` rather than zero, because a percent
that silently reads as 0 is a perk the player paid for and never got.
"""

from decimal import Decimal

from stalbot.domain.shop.effects import (
    EffectKind,
    describe_effect,
    is_automatic,
    parse_amount_range,
    parse_both_percent,
    parse_int,
    parse_percent,
)

# -- percent ----------------------------------------------------------------


def test_parses_a_plain_percent() -> None:
    assert parse_percent("3") == Decimal(3)


def test_parses_a_fractional_percent_with_either_separator() -> None:
    assert parse_percent("1.5") == Decimal("1.5")
    assert parse_percent("1,5") == Decimal("1.5")


def test_an_unreadable_percent_is_none_not_zero() -> None:
    """Zero would be a perk the player paid for and silently did not get."""
    assert parse_percent("много") is None
    assert parse_percent("") is None
    assert parse_percent(None) is None


# -- both_percent -----------------------------------------------------------


def test_parses_the_two_sided_percent() -> None:
    both = parse_both_percent('{"discount": 3, "markup": 1.5}')

    assert both is not None
    assert both.discount == Decimal(3)
    assert both.markup == Decimal("1.5")


def test_malformed_json_is_none() -> None:
    assert parse_both_percent("{не json}") is None
    assert parse_both_percent("[3, 1.5]") is None
    assert parse_both_percent(None) is None


def test_a_half_filled_pair_is_none() -> None:
    """Applying one side of «Золотой абонемент» and not the other is worse than refusing."""
    assert parse_both_percent('{"discount": 3}') is None


# -- amount range -----------------------------------------------------------


def test_parses_a_range() -> None:
    assert parse_amount_range("50-150") == (50, 150)


def test_parses_a_fixed_amount_as_a_degenerate_range() -> None:
    assert parse_amount_range("75") == (75, 75)


def test_a_reversed_range_is_normalised() -> None:
    assert parse_amount_range("150-50") == (50, 150)


def test_a_negative_fixed_amount_is_not_read_as_a_range() -> None:
    """The leading minus is a sign, not a separator."""
    assert parse_amount_range("-5") == (-5, -5)


def test_an_unreadable_range_is_none() -> None:
    assert parse_amount_range("от 50 до 150") is None
    assert parse_amount_range(None) is None


# -- ints -------------------------------------------------------------------


def test_parses_a_whole_number() -> None:
    assert parse_int("96") == 96


def test_an_unreadable_int_is_none() -> None:
    assert parse_int("четверо суток") is None
    assert parse_int(None) is None


# -- descriptions and automation --------------------------------------------


def test_every_known_kind_has_a_label() -> None:
    for kind in EffectKind:
        assert "❓" not in describe_effect(kind, None)


def test_an_unknown_kind_still_describes_itself() -> None:
    """A perk invented between deploys must not render as a blank."""
    assert "своя_штука" in describe_effect("своя_штука", None)


def test_a_description_includes_the_value_when_there_is_one() -> None:
    assert "3" in describe_effect(EffectKind.DISCOUNT_PERCENT, "3")


def test_percent_grant_queue_and_promo_kinds_are_automatic() -> None:
    assert is_automatic(EffectKind.DISCOUNT_PERCENT)
    assert is_automatic(EffectKind.QUEUE_SKIP)
    assert is_automatic(EffectKind.PROMO_CODE)


def test_here_ping_and_manual_are_applied_by_the_admin() -> None:
    """The owner grants @here access by hand (заявка 13.09.2026 п.2, decided 15.09.2026)."""
    assert not is_automatic(EffectKind.HERE_PING)
    assert not is_automatic(EffectKind.MANUAL)


def test_an_unknown_kind_is_not_automatic() -> None:
    """It sells and attaches, but the admin applies it — the honest fallback."""
    assert not is_automatic("своя_штука")
