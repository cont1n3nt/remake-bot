"""Contract test: code constants == the live sheet's own formulas.

PLAN.md §9.1: "тест `test_ladder_matches_sheet_formula` сверяет пороги в
коде с порогами в формуле, чтобы расхождение было поймано в CI, а не в
проде." Extended per sqlite_migration.md §VI.2 ("расширить
`test_ladder_matches_sheet_formula.py` замороженным текстом `K3`/`L3` и
переименовать...") once `calculator.py` existed to have constants worth
freeze-checking beyond just rank/referral thresholds.

The formula text below is a frozen snapshot of `DataBase!R3`/`S3` (captured
live via the Sheets API, 2026-08-02) and `DataBase!F3`/`G3`/`K3`/`L3`
(captured via `scripts/export_sheet_snapshot.py --skupka-xlsx`, 2026-08-10 —
see `tests/fixtures/sheet_snapshot_2026-08-10/formulas.json`) — not
transcribed from PLAN.md's prose, which is exactly what this test exists to
distrust. If the customer edits these formulas, this test is the tripwire:
update the snapshot deliberately, don't just make it pass. After the sheet
is deleted (Э9), this file becomes the *only* surviving record of where the
numbers in `calculator.py`/`perks.py` came from — never delete it.
"""

import re
from typing import Final

from stalbot.domain.progression.perks import (
    BOOSTER_FLAT,
    PURCHASE_TURNOVER_PER_COIN,
    RANK_ONE_TIME_COINS,
    REFEREE_TURNOVER_TIERS,
    REFERRAL_ONE_TIME,
    REFERRED_TIERS,
    SALE_TURNOVER_PER_COIN,
    XP_BOOST_THRESHOLD,
)
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

# Captured via scripts/export_sheet_snapshot.py --skupka-xlsx, 2026-08-10
# (tests/fixtures/sheet_snapshot_2026-08-10/formulas.json).
_F3_FORMULA: Final = (
    '=IF(E3="", "", IF(C3=TRUE, IF(TRUNC(E3/1500000)=0, "", TRUNC(E3/1500000)), '
    'IF(D3=TRUE, IF(TRUNC(E3/2500000)=0, "", TRUNC(E3/2500000)), "")))'
)
_K3_FORMULA: Final = (
    '=IF(J3="", 0, (TRUNC(M3/1500000) + TRUNC(N3/2500000)) '
    "- SUMIF($U$3:$U$852, J3, $V$3:$V$852) "
    "+ IF(SUMIF($H$3:$H$852, J3, $O$3:$O$852)>=1500000, "
    "3 + (SUMIF($H$3:$H$852, J3, $O$3:$O$852)>=5000000)*2 "
    "+ (SUMIF($H$3:$H$852, J3, $O$3:$O$852)>=50000000)*10, 0) "
    '+ IF(IFERROR(VLOOKUP(J3, $B$3:$H$852, 7, FALSE), "")="", 0, '
    "IF(OR(M3>=50000000, N3>=125000000), 15, "
    "IF(OR(M3>=5000000, N3>=12500000), 3, "
    "IF(OR(M3>=1500000, N3>=2500000), 2, 0)))) "
    '+ IF(R3="👑 Legend", 200, IF(R3="💎 Elite", 100, IF(R3="💠 Prestige", 40, '
    'IF(R3="🔷 Premium", 10, IF(R3="🔹 Standard", 5, 0))))) '
    '+ IF(S3="🎩 Рекламный Барон", 150, IF(S3="📢 Амбассадор", 40, '
    'IF(S3="🧲 Вербовщик", 15, IF(S3="📣 Промоутер", 5, IF(S3="🧭 Скаут", 1, 0))))) '
    "+ IF(Q3=TRUE, 3 + SUMPRODUCT(($B$3:$B$852=J3) * "
    "(($C$3:$C$852=TRUE)*($E$3:$E$852>=10000000) "
    "+ ($D$3:$D$852=TRUE)*($E$3:$E$852>=25000000))), 0) "
    "+ SUMPRODUCT(($B$3:$B$852=J3) * (($E$3:$E$852>=100000000) * "
    '((R3="💎 Elite") + (R3="👑 Legend")) * 5 '
    '+ ($E$3:$E$852>=50000000) * (R3="💠 Prestige") * 2)))'
)
_L3_FORMULA: Final = (
    '=IF(J3="", 0, ROUNDUP(((TRUNC(M3/1500000) + TRUNC(N3/2500000))*10 '
    "+ IF(SUMIF($H$3:$H$852, J3, $O$3:$O$852)>=1500000, "
    "20 + (SUMIF($H$3:$H$852, J3, $O$3:$O$852)>=5000000)*20 "
    "+ (SUMIF($H$3:$H$852, J3, $O$3:$O$852)>=50000000)*80, 0) "
    '+ IF(IFERROR(VLOOKUP(J3, $B$3:$H$852, 7, FALSE), "")="", 0, '
    "IF(OR(M3>=50000000, N3>=125000000), 200, "
    "IF(OR(M3>=5000000, N3>=12500000), 40, "
    "IF(OR(M3>=1500000, N3>=2500000), 10, 0)))) "
    '+ IF(S3="📢 Амбассадор", 60, IF(S3="📣 Промоутер", 10, 0)) '
    "+ IF(Q3=TRUE, 30 + SUMPRODUCT(($B$3:$B$852=J3) * "
    "((($C$3:$C$852=TRUE)*($E$3:$E$852>=10000000) "
    "+ ($D$3:$D$852=TRUE)*($E$3:$E$852>=25000000)) * 5)), 0)) "
    "* IF((TRUNC(M3/1500000) + TRUNC(N3/2500000))*10>=250, 1.05, 1), 0))"
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


def test_f3_g3_turnover_rate_matches_perks_constants() -> None:
    assert f"TRUNC(E3/{PURCHASE_TURNOVER_PER_COIN})" in _F3_FORMULA
    assert f"TRUNC(E3/{SALE_TURNOVER_PER_COIN})" in _F3_FORMULA


def test_k3_l3_own_turnover_rate_matches_perks_constants() -> None:
    expected = f"TRUNC(M3/{PURCHASE_TURNOVER_PER_COIN}) + TRUNC(N3/{SALE_TURNOVER_PER_COIN})"
    assert expected in _K3_FORMULA
    assert expected in _L3_FORMULA


def test_k3_rank_one_time_bonus_matches_rank_one_time_coins() -> None:
    ascending = sorted(RANKS, key=lambda tier: tier.xp_required)
    expected = "0"
    for tier in ascending:
        expected = f'IF(R3="{tier.label}", {RANK_ONE_TIME_COINS[tier.key]}, {expected})'
    assert expected in _K3_FORMULA


def test_k3_referral_role_one_time_coins_match_referral_one_time() -> None:
    ascending = sorted(REFERRAL_ROLES, key=lambda tier: tier.referrals_required)
    expected = "0"
    for tier in ascending:
        coins, _xp = REFERRAL_ONE_TIME[tier.key]
        expected = f'IF(S3="{tier.label}", {coins}, {expected})'
    assert expected in _K3_FORMULA


def test_l3_referral_role_xp_matches_referral_one_time() -> None:
    """Only Ambassador/Promoter grant XP on reaching the role — Scout, Вербовщик
    and Барон's `REFERRAL_ONE_TIME` xp component is 0, so they're absent
    from this shorter IF-chain (unlike the 5-tier coins chain in K3)."""
    ambassador = REFERRAL_ONE_TIME["ambassador"][1]
    promoter = REFERRAL_ONE_TIME["promoter"][1]
    assert f'IF(S3="📢 Амбассадор", {ambassador}, IF(S3="📣 Промоутер", {promoter}, 0))' in (
        _L3_FORMULA
    )


def test_k3_referee_turnover_tiers_match_perks_constants() -> None:
    (t1, c1, _x1), (t2, c2, _x2), (t3, c3, _x3) = REFEREE_TURNOVER_TIERS
    expected = (
        f">={t1}, {c1} + "
        f"(SUMIF($H$3:$H$852, J3, $O$3:$O$852)>={t2})*{c2} "
        f"+ (SUMIF($H$3:$H$852, J3, $O$3:$O$852)>={t3})*{c3}"
    )
    assert expected in _K3_FORMULA


def test_l3_referee_turnover_tiers_match_perks_constants() -> None:
    (t1, _c1, x1), (t2, _c2, x2), (t3, _c3, x3) = REFEREE_TURNOVER_TIERS
    expected = (
        f">={t1}, "
        f"{x1} + (SUMIF($H$3:$H$852, J3, $O$3:$O$852)>={t2})*{x2} "
        f"+ (SUMIF($H$3:$H$852, J3, $O$3:$O$852)>={t3})*{x3}"
    )
    assert expected in _L3_FORMULA


def test_k3_referred_tiers_match_perks_constants() -> None:
    (mt1, nt1, c1, _x1), (mt2, nt2, c2, _x2), (mt3, nt3, c3, _x3) = REFERRED_TIERS
    expected = (
        f"IF(OR(M3>={mt1}, N3>={nt1}), {c1}, "
        f"IF(OR(M3>={mt2}, N3>={nt2}), {c2}, "
        f"IF(OR(M3>={mt3}, N3>={nt3}), {c3}, 0)))"
    )
    assert expected in _K3_FORMULA


def test_l3_referred_tiers_match_perks_constants() -> None:
    (mt1, nt1, _c1, x1), (mt2, nt2, _c2, x2), (mt3, nt3, _c3, x3) = REFERRED_TIERS
    expected = (
        f"IF(OR(M3>={mt1}, N3>={nt1}), {x1}, "
        f"IF(OR(M3>={mt2}, N3>={nt2}), {x2}, "
        f"IF(OR(M3>={mt3}, N3>={nt3}), {x3}, 0)))"
    )
    assert expected in _L3_FORMULA


def test_k3_booster_flat_bonus_matches_booster_flat() -> None:
    assert f"IF(Q3=TRUE, {BOOSTER_FLAT[0]} + SUMPRODUCT(" in _K3_FORMULA


def test_l3_booster_flat_bonus_matches_booster_flat() -> None:
    assert f"IF(Q3=TRUE, {BOOSTER_FLAT[1]} + SUMPRODUCT(" in _L3_FORMULA


def test_l3_xp_boost_threshold_and_multiplier_present() -> None:
    assert f"*10>={XP_BOOST_THRESHOLD}, 1.05, 1)" in _L3_FORMULA
