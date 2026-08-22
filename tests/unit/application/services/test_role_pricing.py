"""Tests for `stalbot.application.services.role_pricing.resolve_price_multiplier` (§9.1, п.2)."""

from decimal import Decimal

from stalbot.application.services.role_pricing import resolve_price_multiplier
from stalbot.config.role_pricing import ROLE_PRICE_MULTIPLIERS
from stalbot.domain.progression.ranks import RankLadder


def test_no_rank_role_gives_no_multiplier() -> None:
    ladder = RankLadder()

    tier, multiplier = resolve_price_multiplier(frozenset({999999}), ladder)

    assert tier is None
    assert multiplier == Decimal("1.00")


def test_resolves_the_configured_multiplier_for_a_held_rank() -> None:
    ladder = RankLadder()
    standard = ladder.by_key("standard")
    assert standard is not None

    tier, multiplier = resolve_price_multiplier(frozenset({standard.role_id}), ladder)

    assert tier is standard
    assert multiplier == ROLE_PRICE_MULTIPLIERS["standard"]


def test_picks_the_highest_held_rank_when_several_roles_are_present() -> None:
    """Real members hold exactly one rank role — this just guards the lookup order."""
    ladder = RankLadder()
    premium = ladder.by_key("premium")
    elite = ladder.by_key("elite")
    assert premium is not None and elite is not None

    tier, _multiplier = resolve_price_multiplier(
        frozenset({premium.role_id, elite.role_id}), ladder
    )

    assert tier is elite
