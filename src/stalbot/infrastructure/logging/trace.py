"""Per-interaction trace id propagated through `contextvars` (PLAN.md §12).

A short id is generated once per Discord interaction and threaded through
logs and error embeds implicitly via `contextvars`, instead of being passed
through every function signature: a user can quote the id from an error
embed and an admin can grep the logs for the same value.
"""

import uuid
from contextvars import ContextVar

_current_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    """Generate a new short trace id."""
    return uuid.uuid4().hex[:8]


def set_trace_id(trace_id: str) -> None:
    """Bind *trace_id* to the current context (e.g. the current interaction)."""
    _current_trace_id.set(trace_id)


def current_trace_id() -> str:
    """Return the trace id bound to the current context.

    Returns:
        The bound trace id, or a freshly generated one — bound for the rest
        of the context — if none was set yet (e.g. a background task that
        runs outside any interaction).
    """
    trace_id = _current_trace_id.get()
    if trace_id is None:
        trace_id = new_trace_id()
        _current_trace_id.set(trace_id)
    return trace_id
