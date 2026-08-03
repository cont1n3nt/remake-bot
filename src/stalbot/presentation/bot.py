"""Subclass of `commands.Bot` — the anchor point for cogs and persistent views."""

import logging
from collections.abc import Sequence
from typing import Any, Final

import discord
from discord import app_commands
from discord.ext import commands, tasks

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.application.services.audit import AuditService
from stalbot.application.services.boost_orders import BoostOrderService
from stalbot.application.services.catalog import CatalogService
from stalbot.application.services.health import HealthService
from stalbot.application.services.manual_grants import ManualGrantService
from stalbot.application.services.pricing import PricingService
from stalbot.application.services.profile import ProfileService
from stalbot.application.services.progression import ProgressionService
from stalbot.application.services.screenshots import ScreenshotService
from stalbot.application.services.stats import StatsService
from stalbot.application.services.tickets import TicketService
from stalbot.application.services.transactions import TransactionService
from stalbot.config.settings import Settings
from stalbot.domain.clock import SystemClock
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.idempotency import IdempotencyRepository
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)
from stalbot.infrastructure.cache.repositories.ticket_sessions import TicketSessionsRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.cache.sync import CacheSync
from stalbot.infrastructure.discord.audit_channel import AuditChannelGateway
from stalbot.infrastructure.discord.emoji_resolver import EmojiResolver
from stalbot.infrastructure.discord.role_gateway import DiscordRoleGateway
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.infrastructure.ocr.null import NullOcrGateway
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.presentation.cogs.catalog import CatalogCog
from stalbot.presentation.cogs.health import HealthCog
from stalbot.presentation.cogs.manual import ManualCog
from stalbot.presentation.cogs.pricing import PricingCog
from stalbot.presentation.cogs.profile import ProfileCog
from stalbot.presentation.cogs.stats import StatsCog
from stalbot.presentation.cogs.tag import TagCog
from stalbot.presentation.cogs.tickets.cog import TicketsCog
from stalbot.presentation.cogs.transactions import TransactionsCog
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.errors import on_app_command_error

logger = logging.getLogger(__name__)

#: PLAN.md §12: metrics logged once a minute.
_METRICS_LOG_INTERVAL_SECONDS: Final = 60


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
        #: Populated once the guild's emoji list is available (`on_ready`,
        #: `on_guild_emojis_update`) — empty (all lookups miss) until then.
        self.emoji_resolver = EmojiResolver()
        self._users_sync_loop: tasks.Loop[Any] | None = None
        self._items_sync_loop: tasks.Loop[Any] | None = None
        self._progression_loop: tasks.Loop[Any] | None = None
        self._metrics_loop: tasks.Loop[Any] | None = None
        #: Built by `_setup_cache`, once the startup sync has completed —
        #: `/healthcheck`'s uptime clock (PLAN.md §12, M11).
        self.health_service: HealthService | None = None
        self._startup_warnings: tuple[str, ...] = ()

    async def setup_hook(self) -> None:
        """Open the cache, run the mandatory startup sync, then register commands.

        PLAN.md §8.2 requires the full sync to complete *before* slash
        commands are registered — `tree.sync()` only runs after
        `CacheSync.run_startup_sync()` returns.
        """
        await self._setup_cache()

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

        profile_service = ProfileService(UsersCacheRepository(connection), transactions_repo)
        await self.add_cog(ProfileCog(profile_service, self.embed_factory, self.settings))

        items_repo = ItemsCacheRepository(connection)
        catalog_service = CatalogService(
            self.sheets_client,
            items_repo,
            BoostOrderLinesRepository(connection),
            clock=SystemClock(),
        )
        pricing_service = PricingService(self.sheets_client, items_repo, clock=SystemClock())
        await self.add_cog(
            CatalogCog(
                catalog_service,
                pricing_service,
                items_repo,
                self.emoji_resolver,
                self.embed_factory,
            )
        )
        await self.add_cog(
            PricingCog(pricing_service, items_repo, self.embed_factory, self.settings)
        )

        stats_service = StatsService(transactions_repo, UsersCacheRepository(connection))
        await self.add_cog(StatsCog(stats_service, transactions_repo, self.embed_factory))

        manual_grants = ManualGrantService(
            self.sheets_client,
            transactions_repo,
            UsersCacheRepository(connection),
            ProgressionStateRepository(connection),
            DiscordRoleGateway(self, self.settings.guild_id),
            clock=SystemClock(),
        )
        await self.add_cog(ManualCog(manual_grants, self.progression_service, self.embed_factory))
        await self.add_cog(TagCog(self.embed_factory))

        ticket_service = TicketService(TicketSessionsRepository(connection), clock=SystemClock())
        screenshot_service = ScreenshotService(
            ScreenshotAnalysesRepository(connection),
            NullOcrGateway(),
            self.settings,
            clock=SystemClock(),
        )
        boost_order_service = BoostOrderService(BoostOrderLinesRepository(connection), items_repo)
        tickets_cog = TicketsCog(
            ticket_service,
            screenshot_service,
            boost_order_service,
            transaction_service,
            self.progression_service,
            self.embed_factory,
            self.settings,
            clock=SystemClock(),
        )
        await self.add_cog(tickets_cog)
        for view in tickets_cog.persistent_views():
            self.add_view(view)

        health_service = HealthService(
            self.sheets_client,
            cache_sync,
            UsersCacheRepository(connection),
            self.audit_service,
            ScreenshotAnalysesRepository(connection),
            clock=SystemClock(),
        )
        self.health_service = health_service
        await self.add_cog(
            HealthCog(health_service, self.embed_factory, started_at=SystemClock().now())
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
        self._metrics_loop = tasks.loop(seconds=_METRICS_LOG_INTERVAL_SECONDS)(
            self._run_metrics_log
        )
        self._users_sync_loop.start()
        self._items_sync_loop.start()
        self._metrics_loop.start()
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

    async def _run_metrics_log(self) -> None:
        """Log Sheets/cache/audit counters once a minute (PLAN.md §12, M11)."""
        if self.health_service is None:
            return
        status = await self.health_service.snapshot()
        logger.info(
            "metrics: sheets reads=%d writes=%d, cache hit-rate=%s age=%ss, "
            "audit queue=%d, ocr samples=%d confirmed=%d",
            status.sheets_read_requests,
            status.sheets_write_requests,
            f"{status.cache_hit_rate:.0%}" if status.cache_hit_rate is not None else "n/a",
            f"{status.cache_age_seconds:.0f}" if status.cache_age_seconds is not None else "n/a",
            status.audit_queue_size,
            status.ocr_sample_count,
            status.ocr_confirmed_sample_count,
        )

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
        guild = self.get_guild(self.settings.guild_id)
        if guild is not None:
            self.emoji_resolver.refresh(guild.emojis)

    async def on_guild_emojis_update(
        self,
        guild: discord.Guild,
        before: Sequence[discord.Emoji],
        after: Sequence[discord.Emoji],
    ) -> None:
        """Refresh the emoji cache whenever the guild's custom emoji list changes."""
        if guild.id == self.settings.guild_id:
            self.emoji_resolver.refresh(after)

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
        if self._metrics_loop is not None:
            self._metrics_loop.cancel()
        await self.cache_db.close()
        await super().close()


def _channel_display(interaction: discord.Interaction) -> str:
    channel = interaction.channel
    name = getattr(channel, "name", None)
    return f"#{name}" if name else "DM"


def _format_arguments(interaction: discord.Interaction) -> str:
    values = vars(interaction.namespace)
    return " • ".join(f"{key}={value}" for key, value in values.items())
