"""Characterization tests: `domain.progression.calculator` vs the live sheet
(sqlite_migration.md §VI.2, Э1).

Levels A/B/D prove the transcription is exact against a real snapshot
before anything here is trusted to replace the spreadsheet (§VI). Level C
freezes the referral-bug fix's (§III.2, variant B) real-world impact for
owner review — `referral_fix_diff.csv` lives next to this file.

Deliberately does **not** assert against the plan's own worked examples —
those were computed from an earlier snapshot (06-07.08.2026) and the book
has grown since (§VI.1's Э0 status note). The ground truth here is always
`tests/fixtures/sheet_snapshot_*/users.csv` — what the live formula
actually output at snapshot time — never prose.
"""

import csv
from pathlib import Path
from typing import Final

from stalbot.domain.progression.calculator import compute_progression, deal_reward
from tests.support.sheet_snapshot import (
    build_fixed_aggregates,
    build_legacy_aggregates,
    load_tickets,
    load_users,
)

#: Bump alongside `scripts/export_sheet_snapshot.py` reruns (Э0's "снимок
#: снимается дважды" — Э7's pre-cutover snapshot will need a new constant).
SNAPSHOT_DIR: Final = Path(__file__).resolve().parents[3] / "fixtures" / "sheet_snapshot_2026-08-10"
_REFERRAL_FIX_DIFF_PATH: Final = Path(__file__).with_name("referral_fix_diff.csv")

_tickets = load_tickets(SNAPSHOT_DIR / "tickets.csv")
_users = load_users(SNAPSHOT_DIR / "users.csv")
_legacy = build_legacy_aggregates(_tickets, _users)
_fixed = build_fixed_aggregates(_tickets, _users)


def test_snapshot_fixture_is_not_empty() -> None:
    """Guards against every test below passing vacuously on an empty fixture."""
    assert len(_tickets) > 500
    assert len(_users) > 200


def test_level_a_compute_progression_matches_sheet_with_zero_tolerance() -> None:
    """`compute_progression(legacy_aggregates)` == the sheet's own K/L/M/N/O/P/R/S,
    for every player, with zero tolerance — the main migration risk (§I.2)."""
    mismatches = []
    for user in _users:
        progression = compute_progression(_legacy[user.nick_norm])
        rank_label = progression.rank.label if progression.rank is not None else ""
        role_label = (
            progression.referral_role.label if progression.referral_role is not None else ""
        )
        actual = (
            progression.coins,
            progression.xp,
            progression.purchase_turnover,
            progression.sale_turnover,
            progression.total_turnover,
            progression.referral_count,
            rank_label,
            role_label,
        )
        expected = (
            user.coins,
            user.xp,
            user.purchase_turnover,
            user.sale_turnover,
            user.total_turnover,
            user.referral_count,
            user.rank_label,
            user.referral_role_label,
        )
        if actual != expected:
            mismatches.append((user.nick_norm, expected, actual))

    assert mismatches == []


def test_level_b_deal_reward_matches_sheet_f_g_columns() -> None:
    """`deal_reward(type, amount)` == the sheet's own `F`/`G` for every real deal."""
    mismatches = []
    real_deal_count = 0
    with (SNAPSHOT_DIR / "tickets.csv").open(encoding="utf-8", newline="") as f:
        for ticket, raw in zip(_tickets, csv.DictReader(f), strict=True):
            if ticket.deal_type is None:
                continue
            real_deal_count += 1
            reward = deal_reward(ticket.deal_type, ticket.amount)
            expected_coins = int(float(raw["Coins"])) if raw["Coins"] != "" else 0
            expected_xp = int(float(raw["XP"])) if raw["XP"] != "" else 0
            if (reward.coins, reward.xp) != (expected_coins, expected_xp):
                mismatches.append((ticket.sheet_row, ticket.nick_norm, expected_coins, reward))

    assert real_deal_count > 500
    assert mismatches == []


def test_level_d_aggregation_uses_every_real_deal_not_only_dated_ones() -> None:
    """Regression guard for the bug that dropped 603/657 dateless deals
    (§I.3): aggregation must fold in every real (non-placeholder) ticket
    row, not silently shrink back down to some small subset."""
    real_deal_rows = [t for t in _tickets if t.deal_type is not None]
    placeholder_rows = [t for t in _tickets if t.deal_type is None]
    assert len(real_deal_rows) + len(placeholder_rows) == len(_tickets)
    # The historical bug affected 603 of 657 tickets — assert we're nowhere
    # near that regime. The exact count grows over time (§VI.1's Э0 status
    # note), so this is a floor, not an exact match.
    assert len(real_deal_rows) > 600

    # Every real deal's amount must reach its player's aggregated turnover —
    # sum of aggregated M+N across all users must equal the sum of every
    # real deal's amount, each deal counted exactly once. A dropped or
    # duplicated row would break this equality.
    total_amount_in_aggregates = sum(
        agg.purchase_turnover + agg.sale_turnover for agg in _legacy.values()
    )
    total_amount_in_tickets = sum(t.amount for t in real_deal_rows)
    assert total_amount_in_aggregates == total_amount_in_tickets


def test_level_c_referral_fix_diff_matches_frozen_fixture() -> None:
    """Diff between legacy (level A) and fixed/variant-B (§III.2) progression,
    frozen for owner review in `referral_fix_diff.csv` — regenerate
    deliberately (see the file this test writes on first run), never let it
    silently drift."""
    diff_rows = _compute_referral_fix_diff()

    if not _REFERRAL_FIX_DIFF_PATH.exists():
        _write_referral_fix_diff(diff_rows)

    with _REFERRAL_FIX_DIFF_PATH.open(encoding="utf-8", newline="") as f:
        frozen = list(csv.DictReader(f))

    actual = [
        {
            "nick": row[0],
            "coins_before": str(row[1]),
            "coins_fixed": str(row[2]),
            "xp_before": str(row[3]),
            "xp_fixed": str(row[4]),
            "rank_before": row[5],
            "rank_fixed": row[6],
            "referral_role_before": row[7],
            "referral_role_fixed": row[8],
            "referrals_before": str(row[9]),
            "referrals_fixed": str(row[10]),
        }
        for row in diff_rows
    ]
    assert actual == frozen


_DiffRow = tuple[str, int, int, int, int, str, str, str, str, int, int]


def _compute_referral_fix_diff() -> list[_DiffRow]:
    rows: list[_DiffRow] = []
    for user in sorted(_users, key=lambda u: u.nick_norm):
        legacy = compute_progression(_legacy[user.nick_norm])
        fixed = compute_progression(_fixed[user.nick_norm])
        legacy_rank = legacy.rank.label if legacy.rank is not None else ""
        fixed_rank = fixed.rank.label if fixed.rank is not None else ""
        legacy_role = legacy.referral_role.label if legacy.referral_role is not None else ""
        fixed_role = fixed.referral_role.label if fixed.referral_role is not None else ""
        if (
            legacy.coins,
            legacy.xp,
            legacy_rank,
            legacy_role,
            legacy.referral_count,
        ) != (fixed.coins, fixed.xp, fixed_rank, fixed_role, fixed.referral_count):
            rows.append(
                (
                    user.nick_norm,
                    legacy.coins,
                    fixed.coins,
                    legacy.xp,
                    fixed.xp,
                    legacy_rank,
                    fixed_rank,
                    legacy_role,
                    fixed_role,
                    legacy.referral_count,
                    fixed.referral_count,
                )
            )
    return rows


def _write_referral_fix_diff(rows: list[_DiffRow]) -> None:
    with _REFERRAL_FIX_DIFF_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "nick",
                "coins_before",
                "coins_fixed",
                "xp_before",
                "xp_fixed",
                "rank_before",
                "rank_fixed",
                "referral_role_before",
                "referral_role_fixed",
                "referrals_before",
                "referrals_fixed",
            ]
        )
        writer.writerows(rows)
