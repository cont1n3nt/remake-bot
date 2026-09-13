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
    *,
    players: list[Player] | None = None,
    progressions: list[PlayerProgressionRecord] | None = None,
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


def _component_interaction() -> MagicMock:
    """A button press: answered through `response.send_message`, not a followup."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member, id=1)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def _call_database(
    cog: DatabaseCog, interaction: MagicMock, поиск: str | None = None
) -> None:
    callback: Any = DatabaseCog.database.callback
    await callback(cog, interaction, поиск)


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


async def test_database_single_page_drops_the_arrows_but_keeps_the_browse_buttons() -> None:
    """заявка 13.09.2026 п.5: one page has nothing to page through, but still drills down."""
    cog, *_ = _cog(players=[_player()])
    interaction = _interaction()

    await _call_database(cog, interaction)

    view = interaction.followup.send.call_args.kwargs["view"]
    labels = [item.label for item in view.children]
    assert labels == ["🏅 По рангам", "🤝 По реф-ролям"]


# -- переход по рангам/реф-ролям (заявка 13.09.2026 п.5) -------------------


async def test_browse_offers_one_button_per_tier_with_holder_counts() -> None:
    cog, *_ = _cog(
        players=[_player(id=1), _player(id=2, nick_display="Other")],
        progressions=[
            _progression(player_id=1, rank_key="elite"),
            _progression(player_id=2, rank_key="elite"),
        ],
    )
    interaction = _component_interaction()

    await cog._on_browse(interaction, "rank")

    view = interaction.response.send_message.call_args.kwargs["view"]
    labels = [item.label for item in view.children]
    assert labels == [
        "🔹 Standard (0)",
        "🔷 Premium (0)",
        "💠 Prestige (0)",
        "💎 Elite (2)",
        "👑 Legend (0)",
    ]


async def test_browse_referral_axis_counts_referral_roles() -> None:
    cog, *_ = _cog(
        players=[_player(id=1)],
        progressions=[_progression(player_id=1, rank_key="elite", referral_role_key="scout")],
    )
    interaction = _component_interaction()

    await cog._on_browse(interaction, "referral")

    view = interaction.response.send_message.call_args.kwargs["view"]
    assert view.children[0].label == "🧭 Скаут (1)"


async def test_tier_pick_lists_holders_with_nick_and_discord() -> None:
    cog, *_ = _cog(
        players=[
            _player(id=1, nick_display="Scaryyyyy", discord_id=111),
            _player(id=2, nick_display="Other", discord_id=None),
            _player(id=3, nick_display="NotElite", discord_id=333),
        ],
        progressions=[
            _progression(player_id=1, rank_key="elite"),
            _progression(player_id=2, rank_key="elite"),
            _progression(player_id=3, rank_key="standard"),
        ],
    )
    interaction = _interaction()

    await cog._on_tier_picked(interaction, "rank", "elite")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "Scaryyyyy → <@111>" in description
    assert "Other → Discord не привязан" in description
    assert "NotElite" not in description
    assert "💎 Elite — 2" in (embed.title or "")


async def test_tier_pick_says_so_when_the_tier_is_empty() -> None:
    cog, *_ = _cog(players=[_player()], progressions=[_progression(player_id=1)])
    interaction = _interaction()

    await cog._on_tier_picked(interaction, "rank", "legend")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "пока никого нет" in (embed.description or "")


async def test_roster_shows_the_referrer_nick_not_a_raw_id() -> None:
    cog, *_ = _cog(
        players=[
            _player(id=1, nick_display="Scaryyyyy", referrer_player_id=2),
            _player(id=2, nick_display="Inviter"),
        ]
    )
    interaction = _interaction()

    await _call_database(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "🤝 Реферер: Inviter" in embed.fields[0].value


# -- поиск (заявка 27.08.2026 п.6) -----------------------------------------


async def test_database_search_filters_by_nick_substring() -> None:
    players = [
        _player(id=1, nick_norm=NormalizedNick("scaryyyyy"), nick_display="Scaryyyyy"),
        _player(id=2, nick_norm=NormalizedNick("othernick"), nick_display="OtherNick"),
    ]
    cog, *_ = _cog(players=players)
    interaction = _interaction()

    await _call_database(cog, interaction, "scary")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert [field.name for field in embed.fields] == ["Scaryyyyy"]


async def test_database_search_is_case_insensitive() -> None:
    players = [_player(id=1, nick_norm=NormalizedNick("scaryyyyy"), nick_display="Scaryyyyy")]
    cog, *_ = _cog(players=players)
    interaction = _interaction()

    await _call_database(cog, interaction, "SCARY")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert [field.name for field in embed.fields] == ["Scaryyyyy"]


async def test_database_search_matches_a_discord_mention() -> None:
    players = [
        _player(id=1, discord_id=111, nick_display="Scaryyyyy"),
        _player(id=2, discord_id=222, nick_display="OtherNick"),
    ]
    cog, *_ = _cog(players=players)
    interaction = _interaction()

    await _call_database(cog, interaction, "<@111>")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert [field.name for field in embed.fields] == ["Scaryyyyy"]


async def test_database_search_matches_a_raw_discord_id() -> None:
    players = [
        _player(id=1, discord_id=111, nick_display="Scaryyyyy"),
        _player(id=2, discord_id=222, nick_display="OtherNick"),
    ]
    cog, *_ = _cog(players=players)
    interaction = _interaction()

    await _call_database(cog, interaction, "111")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert [field.name for field in embed.fields] == ["Scaryyyyy"]


async def test_database_search_with_no_matches_reports_nothing_found() -> None:
    cog, *_ = _cog(players=[_player()])
    interaction = _interaction()

    await _call_database(cog, interaction, "нетнигденичего")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Ничего не найдено" in (embed.description or "")


async def test_database_no_search_shows_every_player() -> None:
    players = [
        _player(id=1, nick_display="Scaryyyyy"),
        _player(id=2, nick_display="OtherNick"),
    ]
    cog, *_ = _cog(players=players)
    interaction = _interaction()

    await _call_database(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert {field.name for field in embed.fields} == {"Scaryyyyy", "OtherNick"}
