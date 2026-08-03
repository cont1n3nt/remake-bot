"""Static Discord entity ids that are not exposed via `.env`.

Channels (`LOG_CHANNEL_ID`, `REVIEWS_CHANNEL_ID`) and `GUILD_ID` live in
`Settings` because they are per-deployment environment. This module holds
ids hard-tied to the server structure and progression business rules
(ticket categories, ladder roles), see PLAN.md §9.1, §11.1.
"""

from collections.abc import Mapping
from typing import Final

from stalbot.domain.enums import TicketKind

#: Categories the bot watches for new channels, keyed by the category id.
TICKET_CATEGORIES: Final[Mapping[int, TicketKind]] = {
    1475149130748657841: TicketKind.SELL_ITEMS,
    1503802805801058336: TicketKind.SELL_BOOSTS,
    1479228622014251049: TicketKind.ORDER_BOOSTS,
}

#: Ticket Tool bot id — the ticket panel is posted after its first message in the channel.
TICKET_TOOL_BOT_ID: Final = 557628352828014614

#: Rank role ids, keyed by the future `RankTier.key` (see `domain.progression.ranks`).
RANK_ROLE_IDS: Final[Mapping[str, int]] = {
    "standard": 1518324856549277827,
    "premium": 1518328036137631805,
    "prestige": 1518328037631066232,
    "elite": 1518328222939611166,
    "legend": 1518328324605083698,
}

#: Referral ladder role ids, keyed by the future `ReferralTier.key`.
REFERRAL_ROLE_IDS: Final[Mapping[str, int]] = {
    "scout": 1518583879672270878,
    "promoter": 1518584176054636584,
    "recruiter": 1518584268933300274,
    "ambassador": 1518584424818671687,
    "baron": 1518584494410563625,
}

#: Stage-3 referral role, granted alongside the top referral tier (Baron).
PARTNER_ROLE_ID: Final = 1518584570457358556
