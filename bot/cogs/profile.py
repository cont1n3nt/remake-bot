from discord import app_commands
from discord.ext import commands

from bot.services.user_service import UserService
from bot.services.referral_service import ReferralService
from bot.utils.embeds import profile_embed, referral_embed, error_embed


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

    @app_commands.command(name="profile")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def profile(self, interaction: discord.Interaction, nickname: str) -> None:
        user = self._user_service.get_profile(nickname)
        if user is None:
            await interaction.response.send_message(
                embed=error_embed("Пользователь не найден"),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(embed=profile_embed(user))

    @app_commands.command(name="refer")
    @app_commands.describe(
        your_nick="Ваш ник в таблице",
        referrer_nick="Ник того, кто вас пригласил",
    )
    async def set_referral(
        self,
        interaction: discord.Interaction,
        your_nick: str,
        referrer_nick: str,
    ) -> None:
        try:
            self._user_service.set_referral(your_nick, referrer_nick)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed(str(e)),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=referral_embed(referrer_nick),
        )

    @app_commands.command(name="referrals")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def referrals(self, interaction: discord.Interaction, nickname: str) -> None:
        user = self._user_service.get_referral_info(nickname)
        if user is None:
            await interaction.response.send_message(
                embed=error_embed("Пользователь не найден"),
                ephemeral=True,
            )
            return

        referrals = self._referral_service.get_referral_count(nickname)
        embed = profile_embed(user)
        embed.add_field(name="Приглашено", value=str(user.referral_count), inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot, bot.user_service, bot.referral_service))
