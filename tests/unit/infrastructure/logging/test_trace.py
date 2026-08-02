"""Tests for `stalbot.infrastructure.logging.trace` (PLAN.md §12)."""

import asyncio

from stalbot.infrastructure.logging.trace import current_trace_id, new_trace_id, set_trace_id


def test_new_trace_id_is_short_hex() -> None:
    trace_id = new_trace_id()
    assert len(trace_id) == 8
    int(trace_id, 16)  # raises ValueError if not hex


def test_set_and_get_round_trip() -> None:
    async def scenario() -> str:
        set_trace_id("deadbeef")
        return current_trace_id()

    assert asyncio.run(scenario()) == "deadbeef"


def test_current_trace_id_generates_and_sticks_when_unset() -> None:
    async def scenario() -> tuple[str, str]:
        first = current_trace_id()
        second = current_trace_id()
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second
    assert len(first) == 8


def test_child_task_inherits_a_snapshot_it_cannot_write_back() -> None:
    async def scenario() -> tuple[str, str]:
        set_trace_id("parent01")

        async def child() -> str:
            # asyncio.create_task() copies the context at creation time, so
            # the child starts out seeing the parent's value...
            inherited = current_trace_id()
            assert inherited == "parent01"
            # ...but mutating it inside the child's own (copied) context
            # must not leak back to the parent's context.
            set_trace_id("child01")
            return current_trace_id()

        child_id = await asyncio.create_task(child())
        return current_trace_id(), child_id

    parent_id_after, child_id = asyncio.run(scenario())
    assert child_id == "child01"
    assert parent_id_after == "parent01"
