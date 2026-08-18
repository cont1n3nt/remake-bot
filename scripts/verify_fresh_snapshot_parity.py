"""Ad-hoc: Level A parity check against an arbitrary snapshot dir (not just
`tests/fixtures/sheet_snapshot_2026-08-10`, which `test_sheet_parity.py`
hardcodes). Verifies `compute_progression()` still matches the sheet's own
K/L/M/N/O/P/R/S for every player in a fresh snapshot — same check, portable
`--snapshot-dir`. Safe to delete after running; not part of the test suite.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from stalbot.domain.progression.calculator import compute_progression
from tests.support.sheet_snapshot import build_legacy_aggregates, load_tickets, load_users


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    args = parser.parse_args()

    tickets = load_tickets(args.snapshot_dir / "tickets.csv")
    users = load_users(args.snapshot_dir / "users.csv")
    legacy = build_legacy_aggregates(tickets, users)

    mismatches = []
    for user in users:
        progression = compute_progression(legacy[user.nick_norm])
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

    print(f"Игроков проверено: {len(users)}")
    print(f"Расхождений: {len(mismatches)}")
    for nick, expected, actual in mismatches[:20]:
        print(f"  {nick}: ожидалось {expected}, получено {actual}")


if __name__ == "__main__":
    main()
