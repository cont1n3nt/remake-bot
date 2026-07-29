import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.user_service import UserService
from bot.services.referral_service import ReferralService
from bot.services.role_service import RoleService, RANK_ROLES, REFERRAL_ROLES
from bot.utils.embeds import profile_embed, referral_embed, referrals_embed, error_embed

logger = logging.getLogger("bot")


async def _send_role_notification(
    channel: discord.TextChannel,
    member: discord.Member,
    nickname: str,
    role_name: str,
    role_type: str,
) -> None:
    mention = member.mention
    role_mention = f"<@&{RANK_ROLES.get(role_name, 0)}>" if role_type == "ранговая" else f"<@&{REFERRAL_ROLES.get(role_name, 0)}>"
    msg = (
        f"🎉 **Выдача {role_type} роли!**\n\n"
        f"1️⃣ Пользователь: {mention}\n"
        f"2️⃣ Игровой никнейм: `{nickname}`\n"
        f"3️⃣ Полученная роль: {role_mention}\n\n"
        f"Поздравляем с получением новой роли! 🌟"
    )
    try:
        await channel.send(msg)
    except Exception as e:
        logger.warning("role notification failed: %s", e)


class ProfileCog(commands.Cog):

    def __init__(
        self,
        bot: commands.Bot,
        user_service: UserService,
        referral_service: ReferralService,
    ) -> None:
        self.bot = bot
        self._user_service = user_service
        self._referral_service = referral_service

    @app_commands.command(name="profile", description="👤 Показать профиль пользователя")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def profile(self, interaction: discord.Interaction, nickname: str) -> None:
        await interaction.response.defer(ephemeral=True)

        nickname = nickname.lower().strip()

        try:
            user = await asyncio.to_thread(self._user_service.get_profile, nickname)
        except Exception as e:
            logger.error("profile error by %s: %s", interaction.user, e)
            await interaction.followup.send(
                embed=error_embed("Ошибка при загрузке профиля"),
                ephemeral=True,
            )
            return

        if user is None:
            await interaction.followup.send(
                embed=error_embed("Пользователь не найден"),
                ephemeral=True,
            )
            return

        rank_progress = self._referral_service.get_rank_progress(user.xp)
        rank_bonus = self._referral_service.get_rank_bonus(user.xp)

        await interaction.followup.send(
            embed=profile_embed(user, rank_progress, rank_bonus), ephemeral=True,
        )
        try:
            await self.bot.audit_logger.log(
                interaction.user, "/profile",
                {"Никнейм": nickname},
            )
        except Exception:
            pass

    @app_commands.command(name="refer", description="🔗 (Админ) Привязать реферала вручную с выдачей роли")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        пользователь="Discord-пользователь, которому выдать роль реферала",
        ник_игрока="Ник игрока в таблице",
        ник_пригласившего="Кто пригласил игрока",
    )
    async def set_referral(
        self,
        interaction: discord.Interaction,
        пользователь: discord.Member,
        ник_игрока: str,
        ник_пригласившего: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        ник_игрока = ник_игрока.lower().strip()
        ник_пригласившего = ник_пригласившего.lower().strip()

        if ник_игрока == ник_пригласившего:
            await interaction.followup.send(embed=error_embed("Нельзя указать самого себя"), ephemeral=True)
            return

        try:
            await asyncio.to_thread(
                self._user_service.set_referral, ник_игрока, ник_пригласившего,
            )
        except ValueError as e:
            await interaction.followup.send(embed=error_embed(str(e)), ephemeral=True)
            return
        except Exception as e:
            logger.error("refer error by %s: %s", interaction.user, e)
            await interaction.followup.send(embed=error_embed("Ошибка при установке реферала"), ephemeral=True)
            return

        if interaction.guild:
            ref_count = self._referral_service.get_referral_count(ник_пригласившего)
            target = self._referral_service.get_target_referral_name(ref_count)
            if target:
                role_id = REFERRAL_ROLES.get(target)
                if role_id:
                    role_obj = interaction.guild.get_role(role_id)
                    if role_obj:
                        try:
                            for rid in REFERRAL_ROLES.values():
                                r = interaction.guild.get_role(rid)
                                if r and r in пользователь.roles:
                                    await пользователь.remove_roles(r)
                            await пользователь.add_roles(role_obj)
                            await _send_role_notification(
                                interaction.channel, пользователь,
                                ник_пригласившего, target, "реферальная",
                            )
                        except discord.Forbidden:
                            logger.warning("Нет прав на выдачу реферальной роли")
                        except Exception as e:
                            logger.error("Ошибка выдачи реферальной роли: %s", e)

        await interaction.followup.send(embed=referral_embed(ник_пригласившего), ephemeral=True)
        try:
            await self.bot.audit_logger.log(
                interaction.user, "/refer",
                {"Ник игрока": ник_игрока, "Ник пригласившего": ник_пригласившего,
                 "Discord-пользователь": str(пользователь)},
            )
        except Exception:
            pass

    @set_referral.error
    async def refer_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.error("refer error by %s: %s", interaction.user, error)

        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"

        try:
            await self.bot.audit_logger.log(
                interaction.user, "/refer", {"Ошибка": str(error)}, success=False,
            )
        except Exception:
            pass

        try:
            await interaction.response.send_message(embed=error_embed(text), ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(embed=error_embed(text), ephemeral=True)

    @app_commands.command(name="referrals", description="👥 Показать список рефералов пользователя и дату прикрепления")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def referrals(self, interaction: discord.Interaction, nickname: str) -> None:
        await interaction.response.defer(ephemeral=True)

        nickname = nickname.lower().strip()

        try:
            user = await asyncio.to_thread(self._user_service.get_referral_info, nickname)
        except Exception as e:
            logger.error("referrals error by %s: %s", interaction.user, e)
            await interaction.followup.send(
                embed=error_embed("Ошибка при загрузке рефералов"),
                ephemeral=True,
            )
            return

        if user is None:
            await interaction.followup.send(
                embed=error_embed("Пользователь не найден"),
                ephemeral=True,
            )
            return

        referred_users = await asyncio.to_thread(
            self._referral_service.get_referred_users, nickname,
        )
        count = len(referred_users)
        level = self._referral_service.get_referral_level(count)
        level_name = self._referral_service.get_level_name(level)
        level_bonus = self._referral_service.get_level_bonus(level)
        progress = self._referral_service.get_next_level_progress(count)
        next_bonus = self._referral_service.get_level_bonus(level + 1)

        embed = referrals_embed(
            user, referred_users, level, level_name,
            level_bonus, progress, next_bonus,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        try:
            await self.bot.audit_logger.log(
                interaction.user, "/referrals",
                {"Никнейм": nickname, "Рефералов": count},
            )
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot, bot.user_service, bot.referral_service))
