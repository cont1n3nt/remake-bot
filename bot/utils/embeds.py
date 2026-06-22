from discord import Embed, Colour

from bot.models.user import User


def profile_embed(user: User) -> Embed:
    embed = Embed(
        title=f"Профиль — {user.nickname}",
        colour=Colour.blurple(),
    )
    embed.add_field(name="Монеты", value=str(user.coins))
    embed.add_field(name="XP", value=str(user.xp))
    embed.add_field(name="Ранг", value=user.rank if user.rank else "—")
    embed.add_field(name="Referral role", value=user.referral_role if user.referral_role else "—")
    embed.add_field(name="Приглашено", value=str(user.referral_count))
    if user.referred_by:
        embed.add_field(name="Пришел от", value=user.referred_by)
    return embed


def transaction_confirmation_embed(
    nickname: str,
    tx_type: str,
    amount: float,
) -> Embed:
    label = "Покупка" if tx_type == "buy" else "Продажа"
    embed = Embed(
        title="Сделка зафиксирована",
        colour=Colour.green(),
    )
    embed.add_field(name="Ник", value=nickname)
    embed.add_field(name="Тип", value=label)
    embed.add_field(name="Сумма", value=str(amount))
    return embed


def referral_embed(referrer: str) -> Embed:
    return Embed(
        title="Реферал указан",
        description=f"Вы указали, что вас пригласил: `{referrer}`",
        colour=Colour.blurple(),
    )


def error_embed(message: str) -> Embed:
    return Embed(
        title="Ошибка",
        description=message,
        colour=Colour.red(),
    )
