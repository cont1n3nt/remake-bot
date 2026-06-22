import discord
from discord.ext import commands

from bot.config.settings import settings
from bot.repositories.sheets_repository import SheetsRepository
from bot.services.sheets_service import SheetsService
from bot.services.user_service import UserService
from bot.utils.logger import setup_logging


def main() -> None:
    setup_logging()

    if not settings.is_valid:
        raise RuntimeError("Missing required environment variables")

    repo = SheetsRepository(settings.google_sheets_creds, settings.google_sheets_url)
    sheets_service = SheetsService(repo)
    user_service = UserService(sheets_service)

    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="/", intents=intents)
    bot.sheets_service = sheets_service
    bot.user_service = user_service

    @bot.event
    async def on_ready() -> None:
        ...

    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
