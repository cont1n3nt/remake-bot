import logging

import discord
from discord import Object
from discord.ext import commands

from bot.config.settings import settings
from bot.repositories.sheets_repository import SheetsRepository
from bot.services.audit_logger import AuditLogger
from bot.services.sheets_service import SheetsService
from bot.services.user_service import UserService
from bot.services.referral_service import ReferralService
from bot.utils.logger import setup_logging

logger = logging.getLogger("bot")


def main() -> None:
    setup_logging()

    if not settings.is_valid:
        raise RuntimeError("Missing required environment variables")

    repo = SheetsRepository(
        settings.sheet_name,
        settings.google_sheets_creds,
        settings.google_sheets_url,
    )
    sheets_service = SheetsService(repo)
    user_service = UserService(sheets_service)
    referral_service = ReferralService(repo)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="/", intents=intents)
    bot.repo = repo
    bot.sheets_service = sheets_service
    bot.user_service = user_service
    bot.referral_service = referral_service
    bot.audit_logger = AuditLogger(bot, settings.audit_channel_id)

    @bot.event
    async def on_ready() -> None:
        logger.info("Bot logged in as %s", bot.user)

    @bot.event
    async def on_app_command_completion(interaction: discord.Interaction, command) -> None:
        """Глобальное логирование всех слэш-команд."""
        try:
            await bot.audit_logger.log_command_usage(interaction)
        except Exception:
            pass

    async def setup_hook() -> None:
        await bot.load_extension("bot.cogs.profile")
        await bot.load_extension("bot.cogs.transactions")
        await bot.load_extension("bot.cogs.tickets")
        await bot.load_extension("bot.cogs.roles")
        await bot.load_extension("bot.cogs.items")
        await bot.load_extension("bot.cogs.admin_cmds")
        await bot.load_extension("bot.cogs.analytics")
        guild = Object(id=settings.guild_id)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        logger.info("Cogs loaded, commands synced")

    bot.setup_hook = setup_hook

    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
