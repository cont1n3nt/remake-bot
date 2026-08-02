"""Tests for `stalbot.domain.progression.ladder.Ladder`, via the real ladders."""

from stalbot.domain.progression.ladder import Tier
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder


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


def test_referral_ladder_current_and_progress() -> None:
    ladder = ReferralLadder()
    assert _key(ladder.current(12)) == "recruiter"
    progress = ladder.progress(12)
    assert progress.done == 12 - 7
    assert progress.need == 20 - 7
