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
from stalbot.infrastructure.logging.trace import new_trace_id, set_trace_id
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

    def queue_size(self) -> int:
        """Return the number of events waiting to be delivered.

        Surfaced by `/healthcheck` and the per-minute metrics log
        (PLAN.md §12, M11) — a growing queue means the audit channel can't
        keep up (or delivery is failing and events are piling up).
        """
        return self._queue.qsize()

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
            # This worker is one long-lived `asyncio.Task` for the whole
            # process, so `current_trace_id()`'s "generate once, cache
            # forever" fallback would otherwise stick to whatever the first
            # batch generated for every later delivery-failure log line
            # (INFRA2-3) — each individual `AuditEvent` already carries its
            # own `trace_id` captured at the moment it was recorded, this
            # only affects this worker's own logging about the batch itself.
            set_trace_id(new_trace_id())
            try:
                embeds = [self._embed_factory.audit(event) for event in batch]
                await self._gateway.send_batch(embeds)
            except Exception:
                logger.exception(
                    "audit channel delivery failed for %d event(s); falling back to file log",
                    len(batch),
                )
                for event in batch:
                    logger.info(
                        "audit event — user: %s (%s), channel: %s, command: /%s, "
                        "args: %s, result: %s, duration: %.2fs, trace: %s",
                        event.user_id,
                        event.user_display,
                        event.channel_display,
                        event.command,
                        event.arguments or "—",
                        event.result,
                        event.duration_seconds,
                        event.trace_id,
                    )
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
