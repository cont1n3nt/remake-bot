import re

import discord
from discord import Embed, Colour

from bot.models.user import User
from bot.services.ocr_service import _fmt as _fmt_plain


_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")


def resolve_emoji(emoji_str: str, guild: discord.Guild | None = None) -> str:
    """Convert stored emoji to Discord format <:name:id> using guild emoji list.
    If the emoji is already in Discord format, returns as-is.
    If not found in guild, returns empty string."""
    if not emoji_str:
        return ""
    if _EMOJI_RE.match(emoji_str):
        return emoji_str
    if guild is None:
        return ""
    name = emoji_str.strip(":")
    for e in guild.emojis:
        if e.name == name:
            return str(e)
    return ""


def format_price_change(
    it: dict,
    old_price: float | None,
    new_price: float,
    guild: discord.Guild | None = None,
) -> str:
    """Единый формат строки изменения цены для /setprice, /setboost, /new_price
    и соответствующих аудит-логов: "• emoji Название | old ₽ → new ₽"."""
    e = resolve_emoji(it.get("emoji", ""), guild)
    emoji_str = e + " " if e else ""
    old_str = _fmt_plain(old_price) if old_price is not None else "—"
    return f"• {emoji_str}{it['name']} | {old_str} ₽ → {_fmt_plain(new_price)} ₽"


def _fmt(n: float | int) -> str:
    s = str(int(n)) if isinstance(n, float) and n == int(n) else str(n)
    parts = s.split(".")
    int_part = parts[0]
    groups = []
    while int_part:
        groups.append(int_part[-3:])
        int_part = int_part[:-3]
    parts[0] = " ".join(reversed(groups))
    return ".".join(parts)


def _progress_bar(current: int, total: int, size: int = 10) -> str:
    filled = min(int(current / total * size) if total else 0, size)
    return "🟦" * filled + "⬜" * (size - filled)


def _has(val) -> bool:
    """Return True if value is non-empty (not None, 0, '', '—', 0.0)."""
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip() not in ("", "—", "0")
    return bool(val)


def profile_embed(
    user: User,
    rank_progress: tuple[int, int, str] | None = None,
    rank_bonus: str = "",
) -> Embed:
    embed = Embed(
        title=f"Профиль — {user.nickname}",
        colour=Colour.blurple(),
    )
    embed.add_field(name="\U0001fa99 Coins", value=_fmt(user.coins))
    embed.add_field(name="\u26a1 XP", value=_fmt(user.xp))

    if _has(user.turnover):
        embed.add_field(name="Общий оборот", value=_fmt(user.turnover))

    if _has(user.rank):
        embed.add_field(name="Ранг", value=user.rank)

    if _has(user.referral_role):
        embed.add_field(name="Реферальная роль", value=user.referral_role)

    if _has(user.referral_count):
        embed.add_field(name="Приглашено", value=_fmt(user.referral_count))

    if rank_bonus:
        embed.add_field(name="Бонус текущего ранга", value=rank_bonus, inline=False)

    if rank_progress:
        current, needed, next_name = rank_progress
        bar = _progress_bar(current, needed)
        embed.add_field(
            name=f"До «{next_name}»",
            value=f"{bar} {_fmt(current)} / {_fmt(needed)} XP",
            inline=False,
        )

    if user.booster:
        embed.add_field(name="\U0001f680 Бустер сервера", value="✅")

    if user.referred_by:
        embed.add_field(name="Ник пригласившего", value=user.referred_by)

    return embed


def transaction_confirmation_embed(
    nickname: str,
    tx_type: str,
    amount: float,
    referrer: str | None = None,
) -> Embed:
    label = "Покупка" if tx_type == "buy" else "Продажа"
    embed = Embed(
        title="Сделка зафиксирована",
        colour=Colour.green(),
    )
    embed.add_field(name="Ник", value=nickname)
    embed.add_field(name="Тип", value=label)
    embed.add_field(name="Сумма", value=_fmt(amount))
    if referrer:
        embed.add_field(name="Ник пригласившего", value=referrer)
    return embed


def referral_embed(referrer: str) -> Embed:
    return Embed(
        title="Реферал указан",
        description=f"Вы указали, что вас пригласил: `{referrer}`",
        colour=Colour.blurple(),
    )


def referrals_embed(
    user: User,
    referred_users: list[str],
    level: int,
    level_name: str,
    level_bonus: str,
    next_progress: tuple[int, int, str],
    next_bonus: str,
) -> Embed:
    embed = Embed(
        title=f"Рефералы — {user.nickname}",
        colour=Colour.blurple(),
    )

    level_text = level_name if level_name else "—"
    if level_bonus:
        level_text += f"\n{level_bonus}"
    embed.add_field(name="Уровень", value=level_text, inline=False)

    if referred_users:
        text = "\n".join(f"• {u}" for u in referred_users[:25])
        if len(referred_users) > 25:
            text += f"\n… и ещё {_fmt(len(referred_users) - 25)}"
        embed.add_field(
            name=f"Приглашено ({_fmt(len(referred_users))})",
            value=text,
            inline=False,
        )
    else:
        embed.add_field(name="Приглашено", value="Нет приглашённых", inline=False)

    current, needed, next_name = next_progress
    if next_name:
        bar = _progress_bar(current, needed)
        embed.add_field(
            name=f"До «{next_name}»",
            value=f"{bar} {_fmt(current)}/{_fmt(needed)}",
            inline=False,
        )
        if next_bonus:
            embed.add_field(
                name="\U0001f381 Награда следующей роли",
                value=next_bonus,
                inline=False,
            )

    return embed


def error_embed(message: str) -> Embed:
    return Embed(
        title="Ошибка",
        description=message,
        colour=Colour.red(),
    )
