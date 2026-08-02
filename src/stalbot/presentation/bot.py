"""Subclass of `commands.Bot` — the anchor point for cogs and persistent views."""

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.application.services.audit import AuditService
from stalbot.application.services.progression import ProgressionService
from stalbot.application.services.transactions import TransactionService
from stalbot.config.settings import Settings
from stalbot.domain.clock import SystemClock
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.idempotency import IdempotencyRepository
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.cache.sync import CacheSync
from stalbot.infrastructure.discord.audit_channel import AuditChannelGateway
from stalbot.infrastructure.discord.role_gateway import DiscordRoleGateway
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.presentation.cogs.transactions import TransactionsCog
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

    def __init__(
        self,
        settings: Settings,
        *,
        embed_factory: EmbedFactory,
        cache_db: CacheDb,
        sheets_client: SheetsClient,
    ) -> None:
        """Build the bot with the required intents.

        Args:
            settings: Validated application configuration.
            embed_factory: Builder for every embed the bot sends.
            cache_db: SQLite cache connection owner (not yet connected).
            sheets_client: Sheets access (nothing touches the network yet).
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
        self.cache_db = cache_db
        self.sheets_client = sheets_client
        #: Set by `bootstrap.build_bot` once the client (needed by the
        #: audit gateway) exists; never `None` by the time commands run.
        self.audit_service: AuditService | None = None
        #: Built by `setup_hook` once the cache connection is open.
        self.cache_sync: CacheSync | None = None
        #: Built by `setup_hook` alongside `cache_sync`.
        self.progression_service: ProgressionService | None = None
        self._users_sync_loop: tasks.Loop[Any] | None = None
        self._items_sync_loop: tasks.Loop[Any] | None = None
        self._progression_loop: tasks.Loop[Any] | None = None
        self._startup_warnings: tuple[str, ...] = ()

    async def setup_hook(self) -> None:
        """Open the cache, run the mandatory startup sync, then register commands.

        PLAN.md §8.2 requires the full sync to complete *before* slash
        commands are registered — `tree.sync()` only runs after
        `CacheSync.run_startup_sync()` returns.
        """
        await self._setup_cache()

        self.tree.add_command(_ping)
        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def _setup_cache(self) -> None:
        connection = await self.cache_db.connect()
        transactions_repo = TransactionsCacheRepository(connection)
        cache_sync = CacheSync(
            self.sheets_client,
            items=ItemsCacheRepository(connection),
            users=UsersCacheRepository(connection),
            transactions=transactions_repo,
            clock=SystemClock(),
        )
        self.cache_sync = cache_sync

        report = await cache_sync.run_startup_sync()
        self._startup_warnings = report.warnings

        assert self.audit_service is not None  # noqa: S101 - set synchronously in bootstrap.build_bot
        self.progression_service = ProgressionService(
            UsersCacheRepository(connection),
            ProgressionStateRepository(connection),
            DiscordRoleGateway(self, self.settings.guild_id),
            AuditChannelGateway(self, self.settings.log_channel_id),
            self.audit_service,
            self.embed_factory,
            sheets=self.sheets_client,
            clock=SystemClock(),
        )

        transaction_service = TransactionService(
            self.sheets_client,
            transactions_repo,
            UsersCacheRepository(connection),
            IdempotencyRepository(connection),
            cache_sync,
            clock=SystemClock(),
        )
        await self.add_cog(
            TransactionsCog(
                transaction_service,
                self.progression_service,
                UsersCacheRepository(connection),
                self.embed_factory,
                self.settings,
            )
        )

        # Loop intervals are per-deployment (`Settings`), so the loops are
        # built here rather than with `@tasks.loop(...)` at class scope.
        # `.start()` fires an immediate first tick on top of the sync just
        # above — a harmless one-time extra API call, not a second
        # mandatory-before-registration sync (that guarantee only holds for
        # the awaited call above; a fire-and-forget loop tick cannot provide it).
        self._users_sync_loop = tasks.loop(seconds=self.settings.sync_users_interval_seconds)(
            self._run_users_sync
        )
        self._items_sync_loop = tasks.loop(seconds=self.settings.sync_items_interval_seconds)(
            self._run_items_sync
        )
        self._progression_loop = tasks.loop(seconds=self.settings.progression_poll_seconds)(
            self._run_progression_poll
        )
        self._users_sync_loop.start()
        self._items_sync_loop.start()
        self._progression_loop.start()

    async def _run_users_sync(self) -> None:
        if self.cache_sync is None:
            return
        report = await self.cache_sync.sync_users_and_transactions()
        await self._send_warnings(report.warnings)

    async def _run_items_sync(self) -> None:
        if self.cache_sync is None:
            return
        await self.cache_sync.sync_items()

    async def _run_progression_poll(self) -> None:
        """Background poll over the whole player base (PLAN.md §9.2), no event channel."""
        if self.progression_service is None:
            return
        await self.progression_service.sync()

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Detect a server-boost transition and record it in column `Q` (PLAN.md §9.2)."""
        if before.premium_since == after.premium_since or self.progression_service is None:
            return
        await self.progression_service.sync_booster_flag(after.id, after.premium_since is not None)

    async def _send_warnings(self, warnings: tuple[str, ...]) -> None:
        if not warnings:
            return
        channel = self.get_channel(self.settings.log_channel_id)
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return
        for message in warnings:
            await channel.send(embed=self.embed_factory.warning("⚠️ Формулы Sheets", message))

    async def on_ready(self) -> None:
        """Log a successful connection, start the audit worker, flush startup warnings."""
        user = self.user
        logger.info("logged in as %s (id=%s)", user, user.id if user else None)
        if self.audit_service is not None:
            self.audit_service.start()
        if self._startup_warnings:
            await self._send_warnings(self._startup_warnings)
            self._startup_warnings = ()

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
        """Flush the audit queue, stop sync loops, close the cache, then disconnect."""
        if self.audit_service is not None:
            await self.audit_service.stop()
        if self._users_sync_loop is not None:
            self._users_sync_loop.cancel()
        if self._items_sync_loop is not None:
            self._items_sync_loop.cancel()
        if self._progression_loop is not None:
            self._progression_loop.cancel()
        await self.cache_db.close()
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
