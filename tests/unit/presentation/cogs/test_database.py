"""Tests for `stalbot.presentation.cogs.database.DatabaseCog` (заявка 21.08.2026 п.3)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.entities.player import Player
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.money import format_amount
from stalbot.domain.nick import NormalizedNick
from stalbot.presentation.cogs.database import DatabaseCog
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _player(**overrides: object) -> Player:
    defaults: dict[str, object] = {
        "id": 1,
        "nick_norm": NormalizedNick("scaryyyyy"),
        "nick_display": "Scaryyyyy",
        "discord_id": 111,
        "referrer_player_id": None,
        "is_booster": False,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return Player(**defaults)  # type: ignore[arg-type]


def _progression(**overrides: object) -> PlayerProgressionRecord:
    defaults: dict[str, object] = {
        "player_id": 1,
        "purchase_turnover": 0,
        "sale_turnover": 0,
        "total_turnover": 0,
        "referral_count": 0,
        "coins": 0,
        "xp": 0,
        "rank_key": None,
        "referral_role_key": None,
        "breakdown_json": "{}",
        "calculator_version": 1,
        "computed_at": _NOW,
    }
    defaults.update(overrides)
    return PlayerProgressionRecord(**defaults)  # type: ignore[arg-type]


def _cog(
    *, players: list[Player] | None = None, progressions: list[PlayerProgressionRecord] | None = None
) -> tuple[DatabaseCog, MagicMock, MagicMock]:
    players_repo = MagicMock()
    players_repo.all = AsyncMock(return_value=players if players is not None else [_player()])
    progression_repo = MagicMock()
    progression_repo.all = AsyncMock(return_value=progressions or [])
    cog = DatabaseCog(players_repo, progression_repo, EmbedFactory())
    return cog, players_repo, progression_repo


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member, id=1)
    interaction.user.guild_permissions = MagicMock(administrator=True)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def _call_database(cog: DatabaseCog, interaction: MagicMock) -> None:
    callback: Any = DatabaseCog.database.callback
    await callback(cog, interaction)


async def test_database_shows_every_player_field() -> None:
    player = _player(discord_id=777, is_booster=True, referrer_player_id=9)
    progression = _progression(
        player_id=1,
        coins=1240,
        xp=3780,
        referral_count=2,
        rank_key="elite",
        referral_role_key="recruiter",
        purchase_turnover=500_000,
    )
    cog, *_ = _cog(players=[player], progressions=[progression])
    interaction = _interaction()

    await _call_database(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    field = embed.fields[0]
    assert field.name == "Scaryyyyy"
    assert "<@777>" in field.value
    assert "да" in field.value  # booster
    assert "ID 9" in field.value  # referrer
    assert format_amount(1240, currency=False) in field.value
    assert "💎 Elite" in field.value
    assert "🧲 Вербовщик" in field.value
    assert format_amount(500_000) in field.value


async def test_database_shows_unbound_players_without_progression() -> None:
    cog, *_ = _cog(players=[_player(discord_id=None)], progressions=[])
    interaction = _interaction()

    await _call_database(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    field = embed.fields[0]
    assert "не привязан" in field.value
    assert "🏅 Ранг" not in field.value


async def test_database_paginates_past_the_page_size() -> None:
    players = [_player(id=i, nick_display=f"Player{i}") for i in range(1, 20)]
    cog, *_ = _cog(players=players)
    interaction = _interaction()

    await _call_database(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)


async def test_database_single_page_sends_without_pager() -> None:
    cog, *_ = _cog(players=[_player()])
    interaction = _interaction()

    await _call_database(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert "view" not in kwargs
