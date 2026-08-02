"""Composition root: wires the dependency graph by hand, no DI framework.

Cogs' dependencies (services, repositories) are added in later milestones —
cogs receive them through the constructor rather than reaching into globals.
"""

from stalbot.application.services.audit import AuditService
from stalbot.config.settings import Settings
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.discord.audit_channel import AuditChannelGateway
from stalbot.infrastructure.logging.setup import configure_logging
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.presentation.bot import StalbotBot
from stalbot.presentation.embeds.factory import EmbedFactory


def build_bot(settings: Settings) -> StalbotBot:
    """Build a bot instance ready for `bot.run()`.

    Neither `CacheDb` nor `SheetsClient` touch the network here — both are
    lazy and only connect once `StalbotBot.setup_hook()` runs inside
    discord.py's own event loop.

    Args:
        settings: Validated application configuration.

    Returns:
        Bot instance with its embed factory and audit pipeline wired up.
        Cogs are attached in later milestones.
    """
    configure_logging(log_level=settings.log_level)

    embed_factory = EmbedFactory()
    cache_db = CacheDb(settings.cache_db_path)
    sheets_client = SheetsClient(settings)
    bot = StalbotBot(
        settings,
        embed_factory=embed_factory,
        cache_db=cache_db,
        sheets_client=sheets_client,
    )

    audit_gateway = AuditChannelGateway(bot, settings.log_channel_id)
    bot.audit_service = AuditService(audit_gateway, embed_factory)

    return bot
