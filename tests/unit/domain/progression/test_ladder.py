"""Tests for `stalbot.domain.progression.ladder.Ladder`, via the real ladders."""

from dataclasses import dataclass

import pytest

from stalbot.domain.progression.ladder import Ladder, Tier
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder


@dataclass(frozen=True, slots=True)
class _FakeTier:
    key: str
    label: str
    role_id: int
    perks: tuple[str, ...] = ()


def _key(tier: Tier | None) -> str:
    assert tier is not None
    return tier.key


def test_current_returns_none_below_first_tier() -> None:
    assert RankLadder().current(0) is None
    assert RankLadder().current(49) is None


def test_current_returns_exact_threshold_match() -> None:
    assert _key(RankLadder().current(50)) == "standard"


def test_current_returns_highest_reached_tier() -> None:
    assert _key(RankLadder().current(3780)) == "elite"


def test_current_returns_top_tier_when_maxed() -> None:
    assert _key(RankLadder().current(999_999)) == "legend"


def test_next_returns_first_upcoming_tier() -> None:
    assert _key(RankLadder().next(3780)) == "legend"


def test_next_returns_none_when_maxed_out() -> None:
    assert RankLadder().next(999_999) is None


def test_next_below_first_tier_returns_first_tier() -> None:
    assert _key(RankLadder().next(0)) == "standard"


def test_progress_towards_next_tier() -> None:
    progress = RankLadder().progress(3780)
    assert progress.done == 3780 - 3500
    assert progress.need == 7000 - 3500
    assert progress.pct == round((3780 - 3500) / (7000 - 3500) * 100)


def test_progress_before_first_tier_uses_zero_base() -> None:
    progress = RankLadder().progress(25)
    assert progress.done == 25
    assert progress.need == 50
    assert progress.pct == 50


def test_progress_when_maxed_out_reports_100_percent() -> None:
    progress = RankLadder().progress(999_999)
    assert progress.pct == 100


def test_by_label_finds_exact_match() -> None:
    tier = RankLadder().by_label("💎 Elite")
    assert tier is not None
    assert tier.key == "elite"


def test_by_label_returns_none_for_unknown_label() -> None:
    assert RankLadder().by_label("not a real rank") is None


def test_by_key_finds_exact_match() -> None:
    tier = RankLadder().by_key("elite")
    assert tier is not None
    assert tier.label == "💎 Elite"


def test_by_key_returns_none_for_unknown_key() -> None:
    assert RankLadder().by_key("not-a-real-key") is None


def test_by_role_id_finds_exact_match() -> None:
    ladder = RankLadder()
    elite = ladder.by_label("💎 Elite")
    assert elite is not None
    assert ladder.by_role_id(elite.role_id) is elite


def test_role_ids_covers_every_tier() -> None:
    ladder = RankLadder()
    assert ladder.role_ids == {tier.role_id for tier in ladder.tiers}
    assert len(ladder.role_ids) == 5


def test_tiers_are_sorted_ascending_by_threshold() -> None:
    thresholds = [tier.xp_required for tier in RankLadder().tiers]
    assert thresholds == sorted(thresholds)


def test_threshold_of_returns_the_tiers_unlock_value() -> None:
    ladder = RankLadder()
    elite = ladder.by_label("💎 Elite")
    assert elite is not None
    assert ladder.threshold_of(elite) == 3500


# --- DOM-6/DOM-7: `pct` must stay within [0, 99] while a tier is still ahead ---


def test_progress_clamps_pct_at_zero_for_a_negative_value() -> None:
    """Below the first tier, `base` is 0 — a negative `value` (shouldn't happen
    upstream, but nothing here guarantees it) must not produce a negative pct."""
    progress = RankLadder().progress(-10)
    assert progress.pct == 0


def test_progress_never_reports_100_percent_while_a_tier_is_still_ahead() -> None:
    """6999/7000 XP to "standard"->"legend" rounds to 100% via plain `round()`,
    even though `next()` still reports "legend" as not yet reached (DOM-7)."""
    ladder = RankLadder()
    progress = ladder.progress(6999)
    assert _key(ladder.next(6999)) == "legend"
    assert progress.pct == 99


# --- DOM-9: reject a ladder whose thresholds aren't strictly increasing ---


def test_construction_rejects_duplicate_thresholds() -> None:
    tiers = [_FakeTier("a", "A", 1), _FakeTier("b", "B", 2)]
    with pytest.raises(ValueError, match="strictly increasing"):
        Ladder(tiers, threshold_of=lambda _t: 100)


def test_referral_ladder_current_and_progress() -> None:
    ladder = ReferralLadder()
    assert _key(ladder.current(12)) == "recruiter"
    progress = ladder.progress(12)
    assert progress.done == 12 - 7
    assert progress.need == 20 - 7
