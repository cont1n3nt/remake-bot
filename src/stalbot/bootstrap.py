"""Composition root: wires the dependency graph by hand, no DI framework.

Starting with M2, this module will also wire up `SheetsClient`, `CacheDb`,
repositories and services — cogs receive their dependencies through the
constructor rather than reaching into globals.
"""

from stalbot.application.services.audit import AuditService
from stalbot.config.settings import Settings
from stalbot.infrastructure.discord.audit_channel import AuditChannelGateway
from stalbot.infrastructure.logging.setup import configure_logging
from stalbot.presentation.bot import StalbotBot
from stalbot.presentation.embeds.factory import EmbedFactory


def build_bot(settings: Settings) -> StalbotBot:
    """Build a bot instance ready for `bot.run()`.

    Args:
        settings: Validated application configuration.

    Returns:
        Bot instance with its embed factory and audit pipeline wired up.
        Cogs are attached in later milestones.
    """
    configure_logging(log_level=settings.log_level)

    embed_factory = EmbedFactory()
    bot = StalbotBot(settings, embed_factory=embed_factory)

    audit_gateway = AuditChannelGateway(bot, settings.log_channel_id)
    bot.audit_service = AuditService(audit_gateway, embed_factory)

    return bot
