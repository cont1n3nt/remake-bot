"""Non-blocking audit log delivery (PLAN.md §5.4).

Commands must never wait on Discord message delivery to the audit channel,
so `AuditService.record()` only enqueues the event; a background worker
drains the queue, batches up to `_MAX_EMBEDS_PER_MESSAGE` audit embeds per
Discord message, and hands them to the `AuditGateway` port.

The service owns embed construction (via `EmbedFactory`) rather than the
gateway: `EmbedFactory` lives under `presentation/`, but the audit embed
format (PLAN.md §5.4) is also needed by other background application
services (e.g. the progression poller in M3) that have no cog to delegate
rendering to. Building it once here, instead of duplicating the field
layout inside `infrastructure/discord/audit_channel.py`, keeps the single
audit format single.
"""

import asyncio
import logging

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.application.ports.audit_gateway import AuditGateway
from stalbot.presentation.embeds.factory import EmbedFactory

logger = logging.getLogger(__name__)

_MAX_EMBEDS_PER_MESSAGE = 10


class AuditService:
    """Queues audit events and flushes them to the audit channel in batches."""

    def __init__(self, gateway: AuditGateway, embed_factory: EmbedFactory) -> None:
        """Create the service.

        Args:
            gateway: Delivers already-built embeds to the audit channel.
            embed_factory: Builds the one audit embed format from an event.
        """
        self._gateway = gateway
        self._embed_factory = embed_factory
        self._queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def record(self, event: AuditEvent) -> None:
        """Enqueue *event* for delivery. Never blocks and never raises."""
        self._queue.put_nowait(event)

    def start(self) -> None:
        """Start the background worker.

        Must be called once the event loop is running (e.g. from
        `on_ready`), not at construction time.
        """
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Flush queued events and stop the worker (graceful shutdown)."""
        if self._task is None:
            return
        await self._queue.join()
        self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while True:
            batch = await self._collect_batch()
            try:
                embeds = [self._embed_factory.audit(event) for event in batch]
                await self._gateway.send_batch(embeds)
            except Exception:
                logger.exception(
                    "audit channel delivery failed for %d event(s); falling back to file log",
                    len(batch),
                )
                for event in batch:
                    logger.info("audit event: %s", event)
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _collect_batch(self) -> list[AuditEvent]:
        """Wait for at least one event, then drain up to the batch cap."""
        batch = [await self._queue.get()]
        while len(batch) < _MAX_EMBEDS_PER_MESSAGE:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch
