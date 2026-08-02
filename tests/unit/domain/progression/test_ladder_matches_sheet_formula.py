"""Contract test: code thresholds == the live sheet's own `R`/`S` formulas.

PLAN.md §9.1: "тест `test_ladder_matches_sheet_formula` сверяет пороги в
коде с порогами в формуле, чтобы расхождение было поймано в CI, а не в
проде." The formula text below is a frozen snapshot, captured directly
from `DataBase!R3`/`DataBase!S3` on the real spreadsheet (read-only probe,
2026-08-02) — not transcribed from PLAN.md's prose, which is exactly what
this test exists to distrust. If the customer edits these formulas, this
test is the tripwire: update the snapshot deliberately, don't just make it
pass.
"""

import re
from typing import Final

from stalbot.domain.progression.ranks import RANKS
from stalbot.domain.progression.referrals import REFERRAL_ROLES

# Captured live via `values_batch_get(["DataBase!R3"], params={"valueRenderOption": "FORMULA"})`.
_RANK_FORMULA: Final = (
    '=IF(L3="";"";IF(L3>=7000;"👑 Legend";IF(L3>=3500;"💎 Elite";'
    'IF(L3>=1200;"💠 Prestige";IF(L3>=300;"🔷 Premium";IF(L3>=50;"🔹 Standard";""))))))'
)

# Captured live via `values_batch_get(["DataBase!S3"], params={"valueRenderOption": "FORMULA"})`.
_REFERRAL_FORMULA: Final = (
    '=IF(OR(J3="";NOT(ISNUMBER(P3)));"";IF(P3>=50;"🎩 Рекламный Барон";'
    'IF(P3>=20;"📢 Амбассадор";IF(P3>=7;"🧲 Вербовщик";IF(P3>=3;"📣 Промоутер";'
    'IF(P3>=1;"🧭 Скаут";""))))))'
)

_THRESHOLD_LABEL_RE = re.compile(r">=(\d+);\"([^\"]+)\"")


def _thresholds_from_formula(formula: str) -> dict[str, int]:
    """Extract `{label: threshold}` from a nested `IF(col>=N;"label";...)` formula."""
    return {label: int(threshold) for threshold, label in _THRESHOLD_LABEL_RE.findall(formula)}


def test_rank_thresholds_match_the_live_r_column_formula() -> None:
    formula_thresholds = _thresholds_from_formula(_RANK_FORMULA)
    code_thresholds = {tier.label: tier.xp_required for tier in RANKS}

    assert code_thresholds == formula_thresholds


def test_referral_role_thresholds_match_the_live_s_column_formula() -> None:
    formula_thresholds = _thresholds_from_formula(_REFERRAL_FORMULA)
    code_thresholds = {tier.label: tier.referrals_required for tier in REFERRAL_ROLES}

    assert code_thresholds == formula_thresholds


def test_frozen_formula_snapshot_actually_contains_five_tiers() -> None:
    """Guards against the regex silently matching nothing (a vacuously true contract test)."""
    assert len(_thresholds_from_formula(_RANK_FORMULA)) == 5
    assert len(_thresholds_from_formula(_REFERRAL_FORMULA)) == 5
