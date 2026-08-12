"""Tests for `stalbot.presentation.cogs.transactions.TransactionsCog`.

PLAN.md §10.1; sqlite_migration.md Э7. `TransactionService`/`ProgressionService`/
`PlayersRepository` are mocked — their own behavior is covered elsewhere;
this file is about whether the cog validates input and orchestrates them
correctly. The command's callback is invoked directly
(`TransactionsCog.add.callback`), bypassing discord.py's own dispatch
machinery.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands

from stalbot.application.dto.transaction_request import (
    AddTransactionRequest,
    TransactionRegistrationResult,
)
from stalbot.domain.entities.deal import Deal
from stalbot.domain.entities.player import Player
from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.money import Rub
from stalbot.domain.nick import NormalizedNick
from stalbot.presentation.cogs.transactions import TransactionsCog
from stalbot.presentation.embeds.factory import EmbedFactory

_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _deal(**overrides: object) -> Deal:
    defaults: dict[str, object] = {
        "id": 1,
        "player_id": 1,
        "occurred_at": _NOW,
        "occurred_at_kind": OccurredAtKind.BOT,
        "deal_type": DealType.PURCHASE,
        "amount": Rub(299900),
        "coins": 1,
        "xp": 10,
        "rank_at_deal": None,
        "booster_at_deal": False,
        "recorded_by": 999,
        "source": DealSource.ADD,
        "legacy_sheet_row": None,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Deal(**defaults)  # type: ignore[arg-type]


def _result(**overrides: object) -> TransactionRegistrationResult:
    defaults: dict[str, object] = {
        "deal": _deal(),
        "nick_display": "Scaryyyyy",
        "discord_bound": False,
    }
    defaults.update(overrides)
    return TransactionRegistrationResult(**defaults)  # type: ignore[arg-type]


def _player(**overrides: object) -> Player:
    defaults: dict[str, object] = {
        "id": 1,
        "nick_norm": NormalizedNick("scaryyyyy"),
        "nick_display": "Scaryyyyy",
        "discord_id": None,
        "referrer_player_id": None,
        "is_booster": False,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return Player(**defaults)  # type: ignore[arg-type]


def _cog(
    *,
    register_result: TransactionRegistrationResult | None = None,
    existing_player: Player | None = None,
) -> tuple[TransactionsCog, MagicMock, MagicMock, MagicMock]:
    transactions = MagicMock()
    transactions.register = AsyncMock(return_value=register_result or _result())
    progression = MagicMock()
    progression.sync = AsyncMock(return_value=[])
    players = MagicMock()
    players.get_by_nick = AsyncMock(return_value=existing_player)
    settings = MagicMock(reviews_channel_id=1490342809075716237)
    cog = TransactionsCog(transactions, progression, players, EmbedFactory(), settings)
    return cog, transactions, progression, players


def _interaction(*, channel: MagicMock | None = None) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.user = MagicMock(id=1)
    interaction.id = 555
    interaction.channel = channel or _messageable_channel()
    return interaction


def _messageable_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    return channel


def _member(member_id: int = 999) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.mention = f"<@{member_id}>"
    return member


def _purchase_choice() -> app_commands.Choice[str]:
    return app_commands.Choice(name="Покупка", value=DealType.PURCHASE.value)


async def _call_add(
    cog: TransactionsCog,
    interaction: MagicMock,
    *,
    ник: str = "Scaryyyyy",
    сумма: str = "299900",
    discord_member: MagicMock | None = None,
    реферал_ник: str | None = None,
    реферал_discord: MagicMock | None = None,
) -> None:
    # `.callback` is the raw unbound function (verified at runtime via
    # `inspect.signature`); discord.py's stubs type it as already bound to
    # the cog instance, which does not match this direct-invocation test
    # pattern — `Any` sidesteps fighting the stub rather than sprinkling a
    # `type: ignore` on every argument line.
    callback: Any = TransactionsCog.add.callback
    await callback(
        cog,
        interaction,
        _purchase_choice(),
        ник,
        discord_member or _member(),
        сумма,
        реферал_ник,
        реферал_discord,
    )


async def test_add_writes_and_sends_confirmation_and_public_notice() -> None:
    cog, transactions, progression, _players = _cog()
    channel = _messageable_channel()
    interaction = _interaction(channel=channel)

    await _call_add(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    transactions.register.assert_awaited_once()
    (request,), _ = transactions.register.call_args
    assert isinstance(request, AddTransactionRequest)
    assert request.nick == "Scaryyyyy"
    assert request.deal_type is DealType.PURCHASE
    assert request.amount == Decimal(299900)

    progression.sync.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_add_rejects_referrer_equal_to_self() -> None:
    cog, transactions, _progression, _players = _cog()
    interaction = _interaction()

    await _call_add(cog, interaction, ник="Scaryyyyy", реферал_ник="  scaryyyyy ")

    transactions.register.assert_not_called()
    interaction.followup.send.assert_awaited_once()
    (kwargs,) = [interaction.followup.send.call_args.kwargs]
    embed = kwargs["embed"]
    assert "Реферал не может быть тем же" in (embed.description or "")


async def test_add_warns_when_referrer_nick_given_without_discord() -> None:
    cog, transactions, _progression, _players = _cog()
    interaction = _interaction()

    await _call_add(cog, interaction, реферал_ник="OtherNick", реферал_discord=None)

    transactions.register.assert_awaited_once()
    (request,), _ = transactions.register.call_args
    assert request.referrer_nick == "OtherNick"
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "без его Discord-аккаунта" in (embed.description or "")


async def test_add_shows_coins_and_xp_immediately() -> None:
    cog, _transactions, _progression, _players = _cog(
        register_result=_result(deal=_deal(coins=7, xp=42))
    )
    interaction = _interaction()

    await _call_add(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "7 Coins" in (embed.description or "")
    assert "42 XP" in (embed.description or "")


async def test_add_skips_confirmation_when_nick_unbound() -> None:
    cog, transactions, _progression, _players = _cog(existing_player=_player(discord_id=None))
    interaction = _interaction()

    await _call_add(cog, interaction)

    transactions.register.assert_awaited_once()
    (request,), _ = transactions.register.call_args
    assert request.force_rebind is False


async def test_add_skips_confirmation_when_discord_id_matches() -> None:
    member = _member(999)
    cog, transactions, _progression, _players = _cog(existing_player=_player(discord_id=999))
    interaction = _interaction()

    await _call_add(cog, interaction, discord_member=member)

    transactions.register.assert_awaited_once()
    (request,), _ = transactions.register.call_args
    assert request.force_rebind is False


async def test_add_asks_for_confirmation_on_binding_conflict_and_proceeds_when_confirmed() -> None:
    cog, transactions, _progression, _players = _cog(existing_player=_player(discord_id=111))
    interaction = _interaction()
    cog._confirm_rebind = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await _call_add(cog, interaction, discord_member=_member(999))

    cog._confirm_rebind.assert_awaited_once()
    transactions.register.assert_awaited_once()
    (request,), _ = transactions.register.call_args
    assert request.force_rebind is True


async def test_add_aborts_when_rebind_not_confirmed() -> None:
    cog, transactions, progression, _players = _cog(existing_player=_player(discord_id=111))
    interaction = _interaction()
    cog._confirm_rebind = AsyncMock(return_value=False)  # type: ignore[method-assign]

    await _call_add(cog, interaction, discord_member=_member(999))

    transactions.register.assert_not_called()
    progression.sync.assert_not_called()
    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert embed.title == "Отменено"
