"""`/database` — full player directory, paginated (заявка 21.08.2026 п.3).

Every `players` row plus its materialized progression, one page at a time.
Read-only: `PlayersRepository.all()` + `ProgressionRepository.all()`, no
new state of its own.

заявка 13.09.2026 п.5 added two buttons under the roster — «🏅 По рангам»
and «🤝 По реф-ролям» — that drill down into who currently holds each tier.
They live under this one command rather than as commands of their own,
which is what the owner asked for: one entry point, three levels deep
(роспись → ранг/реф-роль → носители тира).

Holders are read from `player_progression.rank_key`, not from who actually
wears the Discord role. That is deliberate: this answers "кому ранг
положен по расчёту", so a mismatch with the live roles shows up here as a
discrepancy instead of being hidden by reading the roles back.
"""

import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Final, Literal

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.money import format_amount
from stalbot.domain.progression.ladder import Ladder, Tier
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.base import AuthorLockedView
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_PAGE_SIZE: Final = 8
_HOLDERS_PAGE_SIZE: Final = 15
_MENTION_RE = re.compile(r"^<@!?(\d+)>$")

#: Which ladder a drill-down is about. Not an enum — it exists only to key
#: the two buttons and never leaves this module.
Axis = Literal["rank", "referral"]

_AXIS_TITLE: Final[dict[Axis, str]] = {
    "rank": "🏅 Ранги",
    "referral": "🤝 Реферальные роли",
}

_BrowseHandler = Callable[[discord.Interaction, Axis], Awaitable[None]]
_TierHandler = Callable[[discord.Interaction, Axis, str], Awaitable[None]]


class DatabaseCog(commands.Cog):
    """`/database` — the full player roster, browsable page by page."""

    def __init__(
        self,
        players: PlayersRepository,
        progression: ProgressionRepository,
        embeds: EmbedFactory,
        *,
        rank_ladder: RankLadder | None = None,
        referral_ladder: ReferralLadder | None = None,
    ) -> None:
        """Wire the cog to its read-only collaborators.

        Args:
            players: Source of every `players` row.
            progression: Source of each player's materialized progression.
            embeds: Builds every embed this cog sends.
            rank_ladder: Defaults to a fresh `RankLadder()`.
            referral_ladder: Defaults to a fresh `ReferralLadder()`.
        """
        self._players = players
        self._progression = progression
        self._embeds = embeds
        self._rank_ladder = rank_ladder or RankLadder()
        self._referral_ladder = referral_ladder or ReferralLadder()

    @app_commands.command(name="database", description="🛡️ [Админ] 🗄️ Полная база игроков")
    @app_commands.describe(
        поиск="Фильтр по нику (часть) или Discord (@упоминание/ID) — необязательно"
    )
    @admin_only()
    async def database(self, interaction: discord.Interaction, поиск: str | None = None) -> None:
        """Handle `/database`: page through every player, optionally filtered by nick/Discord."""
        await interaction.response.defer(ephemeral=True)
        players = await self._players.all()
        if поиск:
            players = _filter_players(players, поиск)
        progressions = {record.player_id: record for record in await self._progression.all()}

        empty_message = "Ничего не найдено." if поиск else "Пока нет игроков."
        pages = self._build_pages(players, progressions, empty_message)
        view = _RosterView(pages=pages, author_id=interaction.user.id, on_browse=self._on_browse)
        message = await interaction.followup.send(
            embed=view.current, view=view, ephemeral=True, wait=True
        )
        view.message = message

    # -- drill-down: ладдер -> тир -> носители (заявка 13.09.2026 п.5) -------

    async def _on_browse(self, interaction: discord.Interaction, axis: Axis) -> None:
        """«🏅 По рангам» / «🤝 По реф-ролям» — offer one button per tier."""
        counts = await self._holder_counts(axis)
        ladder = self._ladder(axis)
        total = sum(counts.values())

        lines = [
            f"Всего с {'рангом' if axis == 'rank' else 'реф-ролью'}: {total}.",
            "Выберите тир, чтобы увидеть носителей.",
        ]
        embed = self._embeds.info(_AXIS_TITLE[axis], "\n".join(lines))
        view = _TierPickerView(
            axis=axis,
            tiers=ladder.tiers,
            counts=counts,
            author_id=interaction.user.id,
            on_pick=self._on_tier_picked,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    async def _on_tier_picked(
        self, interaction: discord.Interaction, axis: Axis, tier_key: str
    ) -> None:
        """A tier button — list everyone the calculator currently puts on it."""
        await interaction.response.defer(ephemeral=True)
        tier = self._ladder(axis).by_key(tier_key)
        assert tier is not None  # noqa: S101 - the buttons are built from this same ladder

        holders = await self._holders(axis, tier_key)
        pages = self._build_holder_pages(tier.label, holders)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    def _ladder(self, axis: Axis) -> Ladder[Tier]:
        ladder: Ladder[Tier] = (
            self._rank_ladder if axis == "rank" else self._referral_ladder  # type: ignore[assignment]
        )
        return ladder

    async def _holder_counts(self, axis: Axis) -> dict[str, int]:
        """`tier key -> how many players the calculator puts on it`."""
        counts: dict[str, int] = {}
        for record in await self._progression.all():
            key = _tier_key_of(record, axis)
            if key is not None:
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def _holders(self, axis: Axis, tier_key: str) -> list[Player]:
        """Every player currently on *tier_key*, ordered by nick."""
        player_ids = {
            record.player_id
            for record in await self._progression.all()
            if _tier_key_of(record, axis) == tier_key
        }
        holders = [player for player in await self._players.all() if player.id in player_ids]
        holders.sort(key=lambda player: player.nick_display.casefold())
        return holders

    def _build_holder_pages(
        self, tier_label: str, holders: Sequence[Player]
    ) -> list[discord.Embed]:
        entries = [
            f" • {player.nick_display} → "
            + (f"<@{player.discord_id}>" if player.discord_id else "Discord не привязан")
            for player in holders
        ]
        chunks = _chunk(entries, _HOLDERS_PAGE_SIZE) or [[]]
        title = f"{tier_label} — {len(holders)}"
        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            page_title = title if len(chunks) == 1 else f"{title} (стр. {index}/{len(chunks)})"
            body = "\n".join(chunk) if chunk else "На этом тире пока никого нет."
            pages.append(enforce_limits(self._embeds.info(page_title, body)))
        return pages

    # -- roster pages -------------------------------------------------------

    def _build_pages(
        self,
        players: Sequence[Player],
        progressions: dict[int, PlayerProgressionRecord],
        empty_message: str = "Пока нет игроков.",
    ) -> list[discord.Embed]:
        nicks_by_id = {
            player.id: player.nick_display for player in players if player.id is not None
        }
        chunks = _chunk(players, _PAGE_SIZE) or [()]
        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            title = (
                "🗄️ База игроков"
                if len(chunks) == 1
                else f"🗄️ База игроков (стр. {index}/{len(chunks)})"
            )
            if not chunk:
                pages.append(self._embeds.info(title, empty_message))
                continue
            embed = self._embeds.info(title)
            for player in chunk:
                assert player.id is not None  # noqa: S101 - a fetched player always has an id
                embed.add_field(
                    name=player.nick_display,
                    value=self._format_player(player, progressions.get(player.id), nicks_by_id),
                    inline=False,
                )
            pages.append(enforce_limits(embed))
        return pages

    def _format_player(
        self,
        player: Player,
        progression: PlayerProgressionRecord | None,
        nicks_by_id: dict[int, str],
    ) -> str:
        lines = [
            f"💬 Discord: {f'<@{player.discord_id}>' if player.discord_id else 'не привязан'}",
            f"🚀 Буст сервера: {'да' if player.is_booster else 'нет'}",
        ]
        if player.referrer_player_id is not None:
            # A raw "ID 12" told the reader nothing; the nick is right here
            # in the same roster. Falls back to the id only when the
            # referrer is filtered out of this particular page's result.
            referrer = nicks_by_id.get(player.referrer_player_id)
            lines.append(f"🤝 Реферер: {referrer or f'ID {player.referrer_player_id}'}")
        if progression is not None:
            rank = self._rank_ladder.by_key(progression.rank_key) if progression.rank_key else None
            referral_role = (
                self._referral_ladder.by_key(progression.referral_role_key)
                if progression.referral_role_key
                else None
            )
            lines.append(
                f"🪙 {format_amount(progression.coins, currency=False)} Coins • "
                f"⚡ {format_amount(progression.xp, currency=False)} XP"
            )
            if rank is not None:
                lines.append(f"🏅 Ранг: {rank.label}")
            if referral_role is not None:
                lines.append(f"🤝 Реф-роль: {referral_role.label}")
            # заявка 13.09.2026 п.8: same "omit, don't show a zero" rule the
            # turnovers below already follow, and `/profile` now follows too.
            if progression.referral_count:
                lines.append(f"👥 Приглашено: {progression.referral_count}")
            if progression.purchase_turnover:
                lines.append(f"📤 Оборот продаж: {format_amount(progression.purchase_turnover)}")
            if progression.sale_turnover:
                lines.append(f"📥 Оборот покупок: {format_amount(progression.sale_turnover)}")
            if progression.total_turnover:
                lines.append(f"💹 Общий оборот: {format_amount(progression.total_turnover)}")
        lines.append(f"🕒 Зарегистрирован: {format_datetime(player.created_at)}")
        return "\n".join(lines)


class _RosterView(PaginatedEmbedView):
    """The roster pager plus the two drill-down buttons (заявка 13.09.2026 п.5)."""

    def __init__(
        self,
        *,
        pages: Sequence[discord.Embed],
        author_id: int,
        on_browse: _BrowseHandler,
    ) -> None:
        """Build the view.

        Args:
            pages: Roster pages, in order.
            author_id: The only Discord user allowed to press a button.
            on_browse: Called with the interaction and the chosen axis.
        """
        super().__init__(pages=pages, author_id=author_id)
        if len(pages) == 1:
            # A single page has nothing to page through, and two permanently
            # disabled arrows next to the live buttons just read as broken.
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)
        self.add_item(_BrowseButton("🏅 По рангам", "rank", on_browse))
        self.add_item(_BrowseButton("🤝 По реф-ролям", "referral", on_browse))


class _BrowseButton(discord.ui.Button["_RosterView"]):
    def __init__(self, label: str, axis: Axis, on_browse: _BrowseHandler) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=1)
        self._axis = axis
        self._on_browse = on_browse

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_browse(interaction, self._axis)


class _TierPickerView(AuthorLockedView):
    """One button per ladder tier, with its current holder count."""

    def __init__(
        self,
        *,
        axis: Axis,
        tiers: Sequence[Tier],
        counts: dict[str, int],
        author_id: int,
        on_pick: _TierHandler,
    ) -> None:
        """Build the picker.

        Args:
            axis: Which ladder these tiers belong to.
            tiers: The ladder's tiers, in order.
            counts: `tier key -> holder count`, shown on each button.
            author_id: The only Discord user allowed to press a button.
            on_pick: Called with the interaction, the axis, and the tier key.
        """
        super().__init__(author_id=author_id)
        for tier in tiers:
            self.add_item(_TierButton(axis, tier, counts.get(tier.key, 0), on_pick))


class _TierButton(discord.ui.Button["_TierPickerView"]):
    def __init__(self, axis: Axis, tier: Tier, count: int, on_pick: _TierHandler) -> None:
        super().__init__(
            label=f"{tier.label} ({count})",
            style=discord.ButtonStyle.secondary if count == 0 else discord.ButtonStyle.primary,
        )
        self._axis = axis
        self._tier_key = tier.key
        self._on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_pick(interaction, self._axis, self._tier_key)


def _tier_key_of(record: PlayerProgressionRecord, axis: Axis) -> str | None:
    return record.rank_key if axis == "rank" else record.referral_role_key


def _filter_players(players: Sequence[Player], query: str) -> Sequence[Player]:
    """Filter by nick substring, or by Discord id/mention (заявка 27.08.2026 п.6).

    Args:
        players: The full roster to filter.
        query: Free text — part of a nick, a raw Discord id, or a `<@id>` mention.
    """
    query_norm = query.strip()
    mention = _MENTION_RE.match(query_norm)
    if mention:
        discord_id: int | None = int(mention.group(1))
    elif query_norm.isdigit():
        discord_id = int(query_norm)
    else:
        discord_id = None
    query_casefold = query_norm.casefold()
    return [
        player
        for player in players
        if (discord_id is not None and player.discord_id == discord_id)
        or query_casefold in player.nick_display.casefold()
        or query_casefold in player.nick_norm.casefold()
    ]


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
