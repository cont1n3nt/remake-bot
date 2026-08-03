"""Structured logging setup: JSON to stdout + a rotating file (PLAN.md §2, §12).

Both `structlog`-originated logs and plain `logging.getLogger(__name__)`
calls used throughout the codebase are routed through the same JSON
formatter, so every log line carries the same shape, including the current
trace id (`infrastructure.logging.trace`).
"""

import logging
import logging.handlers
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import structlog

from stalbot.infrastructure.logging.trace import current_trace_id

_DEFAULT_LOG_FILE: Path = Path("./logs/stalbot.log")
_MAX_BYTES: int = 10 * 1024 * 1024
_BACKUP_COUNT: int = 5


def _add_trace_id(
    logger: Any,  # noqa: ANN401 - matches structlog's own `Processor` signature
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Attach the current trace id to every log record."""
    event_dict.setdefault("trace_id", current_trace_id())
    return event_dict


def configure_logging(*, log_level: str = "INFO", log_file: Path = _DEFAULT_LOG_FILE) -> None:
    """Configure `structlog` and stdlib `logging` to emit JSON everywhere.

    Args:
        log_level: Minimum level for the root logger (`Settings.log_level`).
        log_file: Path to the rotating log file; parent directories are
            created if missing.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_trace_id,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [stream_handler, file_handler]
    root.setLevel(log_level)
