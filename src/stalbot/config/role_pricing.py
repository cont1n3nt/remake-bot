"""Per-rank price multipliers, applied to boost-order totals (§9.1, заявка от 21.08.2026).

2026-08-27: заказчик pasted the pre-sqlite economy spec text (the same
human-written description `domain/progression/perks.py`'s docstring already
flags as disagreeing with the live sheet formula "in several places" for
XP/Coins numbers). Unlike those, the discount/markup percentages were never
backed by any spreadsheet formula at all — `perks.py`'s own comment calls
this exact copy "illustrative... deliberately left... rather than invented
here" — so there is no more-authoritative source to defer to for this one
piece; the pasted text is the only real value that ever existed for it.
Applied as given:

    Standard 0% · Premium 0.5% · Prestige 1.5% · Elite 3% · Legend 5%

The text also describes a separate 0.5% Ambassador (referral-role) boost
discount, and Baron's personal promo code — neither is wired in yet (a
referral-role discount isn't the same lookup as this rank table, and a
personal-code-per-Baron is really a `/coupon_add` variant) — flagged for a
follow-up, not silently added here.
"""

from decimal import Decimal
from typing import Final

#: `1.00` = no markup/discount. Reuses the existing rank ladder
#: (`domain/progression/ranks.py`) as the "role" — no separate role system.
ROLE_PRICE_MULTIPLIERS: Final[dict[str, Decimal]] = {
    "standard": Decimal("1.00"),
    "premium": Decimal("0.995"),
    "prestige": Decimal("0.985"),
    "elite": Decimal("0.97"),
    "legend": Decimal("0.95"),
}
