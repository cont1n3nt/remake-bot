"""Subclass of `commands.Bot` — the anchor point for cogs and persistent views."""

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.application.services.audit import AuditService
from stalbot.config.settings import Settings
from stalbot.domain.clock import SystemClock
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.errors import on_app_command_error

logger = logging.getLogger(__name__)


class _StalbotCommandTree(app_commands.CommandTree["StalbotBot"]):
    """Routes every slash-command error through the one global handler."""

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Delegate to `presentation.errors.on_app_command_error`."""
        await on_app_command_error(interaction, error, embeds=self.client.embed_factory)


class StalbotBot(commands.Bot):
    """Stalzone bot: a thin wrapper around `discord.py`.

    All business logic lives in `application/services`; cogs (added starting
    with M4) only wire Discord events to use-case services.
    """

    def __init__(self, settings: Settings, *, embed_factory: EmbedFactory) -> None:
        """Build the bot with the required intents.

        Args:
            settings: Validated application configuration.
            embed_factory: Builder for every embed the bot sends.
        """
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=_StalbotCommandTree,
        )
        self.settings = settings
        self.embed_factory = embed_factory
        #: Set by `bootstrap.build_bot` once the client (needed by the
        #: audit gateway) exists; never `None` by the time commands run.
        self.audit_service: AuditService | None = None

    async def setup_hook(self) -> None:
        """Register commands and sync them to the configured guild."""
        self.tree.add_command(_ping)
        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        """Log a successful connection and start the audit worker."""
        user = self.user
        logger.info("logged in as %s (id=%s)", user, user.id if user else None)
        if self.audit_service is not None:
            self.audit_service.start()

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command[Any, ..., Any] | app_commands.ContextMenu,
    ) -> None:
        """Record a successful command invocation to the audit log (PLAN.md §5.4)."""
        if self.audit_service is None:
            return
        name = (
            f"/{command.qualified_name}"
            if isinstance(command, app_commands.Command)
            else command.name
        )
        self.audit_service.record(
            AuditEvent(
                user_id=interaction.user.id,
                user_display=str(interaction.user),
                channel_display=_channel_display(interaction),
                command=name,
                arguments=_format_arguments(interaction),
                result="Успешно",
                duration_seconds=(discord.utils.utcnow() - interaction.created_at).total_seconds(),
                trace_id=current_trace_id(),
                occurred_at=SystemClock().now(),
            )
        )

    async def close(self) -> None:
        """Flush the audit queue before disconnecting."""
        if self.audit_service is not None:
            await self.audit_service.stop()
        await super().close()


def _channel_display(interaction: discord.Interaction) -> str:
    channel = interaction.channel
    name = getattr(channel, "name", None)
    return f"#{name}" if name else "DM"


def _format_arguments(interaction: discord.Interaction) -> str:
    values = vars(interaction.namespace)
    return " • ".join(f"{key}={value}" for key, value in values.items())


@app_commands.command(name="ping", description="🏓 Проверка связи и логирования")
async def _ping(interaction: discord.Interaction) -> None:
    """Temporary M1 diagnostic command.

    Proves the whole chain — command → audit queue → `EmbedFactory.audit()`
    → log channel — actually works. Superseded by `/healthcheck` in M11.
    """
    bot = interaction.client
    assert isinstance(bot, StalbotBot)  # noqa: S101 - narrows client for embed_factory access
    embed = bot.embed_factory.success("🏓 Понг!", "Бот на связи, логирование работает.")
    await interaction.response.send_message(embed=embed, ephemeral=True)
