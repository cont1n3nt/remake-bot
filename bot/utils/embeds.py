from typing import Optional

from discord import Embed, Colour

from bot.models.user import User


def profile_embed(user: User) -> Embed:
    embed = Embed(
        title=f"Профиль — {user.nickname}",
        colour=Colour.blurple(),
    )
    embed.add_field(name="Монеты", value=str(user.coins))
    embed.add_field(name="XP", value=str(user.xp))
    embed.add_field(name="Уровень", value=str(user.level))
    if user.referral_code:
        embed.add_field(name="Реферальный код", value=user.referral_code)
    embed.add_field(name="Приглашено", value=str(user.referral_count))
    return embed


def transaction_confirmation_embed(
    discord_id: str,
    nickname: str,
    tx_type: str,
    amount: float,
) -> Embed:
    embed = Embed(
        title="Сделка зафиксирована",
        colour=Colour.green(),
    )
    embed.add_field(name="Discord ID", value=discord_id)
    embed.add_field(name="Nickname", value=nickname)
    embed.add_field(name="Тип", value=tx_type)
    embed.add_field(name="Сумма", value=str(amount))
    return embed


def referral_embed(code: str) -> Embed:
    return Embed(
        title="Реферальный код установлен",
        description=f"Ваш код: `{code}`",
        colour=Colour.blurple(),
    )


def error_embed(message: str) -> Embed:
    return Embed(
        title="Ошибка",
        description=message,
        colour=Colour.red(),
    )
