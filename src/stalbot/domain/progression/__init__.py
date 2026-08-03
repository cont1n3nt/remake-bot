"""Rank and referral progression ladders (see PLAN.md §9).

Pure UI/display logic only — the sheet's own formulas are the source of
truth for a player's actual rank/referral role (decision A2). Nothing here
computes a rank; `Ladder.current()` only mirrors what the formula would say,
for progress bars, and is cross-checked against the live formula by a
contract test.
"""
