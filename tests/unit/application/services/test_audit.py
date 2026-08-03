"""Tests for `stalbot.application.services.audit` (PLAN.md §5.4)."""

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime

import discord
import pytest

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.application.services.audit import AuditService
from stalbot.domain.clock import GMT3
from stalbot.presentation.embeds.factory import EmbedFactory

NOW = datetime(2026, 7, 31, 21, 45, tzinfo=GMT3)


class _FakeClock:
    def now(self) -> datetime:
        return NOW


class _FakeGateway:
    """Records delivered batches; can be told to fail the first N calls."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.batches: list[int] = []
        self.attempts = 0
        self._fail_times = fail_times

    async def send_batch(self, embeds: Sequence[discord.Embed]) -> None:
        self.attempts += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("channel unavailable")
        self.batches.append(len(embeds))


def _make_event(trace_id: str) -> AuditEvent:
    return AuditEvent(
        user_id=1,
        user_display="@x",
        channel_display="#c",
        command="/ping",
        arguments="",
        result="Успешно",
        duration_seconds=0.1,
        trace_id=trace_id,
        occurred_at=NOW,
    )


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise TimeoutError("condition was not met in time")
        await asyncio.sleep(0)


@pytest.fixture
def factory() -> EmbedFactory:
    return EmbedFactory(clock=_FakeClock())


async def test_record_delivers_single_event(factory: EmbedFactory) -> None:
    gateway = _FakeGateway()
    service = AuditService(gateway, factory)
    service.record(_make_event("t1"))
    service.start()
    await service.stop()
    assert gateway.batches == [1]


async def test_batches_are_capped_at_ten(factory: EmbedFactory) -> None:
    gateway = _FakeGateway()
    service = AuditService(gateway, factory)
    for i in range(23):
        service.record(_make_event(f"t{i}"))
    service.start()
    await service.stop()
    assert gateway.batches == [10, 10, 3]


async def test_delivery_failure_does_not_crash_worker(factory: EmbedFactory) -> None:
    gateway = _FakeGateway(fail_times=1)
    service = AuditService(gateway, factory)
    service.record(_make_event("fail"))
    service.start()
    await _wait_until(lambda: gateway.attempts >= 1)

    service.record(_make_event("ok"))
    await service.stop()

    assert gateway.attempts == 2
    assert gateway.batches == [1]


async def test_stop_before_start_is_a_no_op(factory: EmbedFactory) -> None:
    gateway = _FakeGateway()
    service = AuditService(gateway, factory)
    await service.stop()
    assert gateway.batches == []


def test_queue_size_reflects_unconsumed_events(factory: EmbedFactory) -> None:
    service = AuditService(_FakeGateway(), factory)
    assert service.queue_size() == 0

    service.record(_make_event("t1"))
    service.record(_make_event("t2"))

    assert service.queue_size() == 2
