"""Per-rank price multipliers, applied to boost-order totals (§9.1, заявка от 21.08.2026).

The заказчик asked for role-based markups/discounts on totals without
giving actual percentages yet — this table is the one place to fill them in
once the numbers arrive: `RankTier.key -> multiplier`, `1.00` meaning "no
change". Reuses the existing rank ladder (`domain/progression/ranks.py`) as
the "role" — no separate role system.
"""

from decimal import Decimal
from typing import Final

#: `1.00` everywhere = no markup/discount yet. Edit in place once the
#: заказчик sends real percentages — e.g. `Decimal("0.95")` for a 5%
#: discount, `Decimal("1.10")` for a 10% markup.
ROLE_PRICE_MULTIPLIERS: Final[dict[str, Decimal]] = {
    "standard": Decimal("1.00"),
    "premium": Decimal("1.00"),
    "prestige": Decimal("1.00"),
    "elite": Decimal("1.00"),
    "legend": Decimal("1.00"),
}
