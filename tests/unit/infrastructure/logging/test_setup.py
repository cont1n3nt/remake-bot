"""Tests for `stalbot.infrastructure.logging.setup` (PLAN.md §2, §12)."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from stalbot.infrastructure.logging.setup import configure_logging
from stalbot.infrastructure.logging.trace import set_trace_id


@pytest.fixture(autouse=True)
def _reset_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    for handler in root.handlers:
        handler.close()
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_configure_logging_writes_json_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "sub" / "stalbot.log"
    configure_logging(log_level="INFO", log_file=log_file)

    set_trace_id("abc12345")
    logging.getLogger("stalbot.test").info("hello", extra={"foo": "bar"})

    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_file.exists()
    lines = [line for line in log_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["event"] == "hello"
    assert record["trace_id"] == "abc12345"
    assert record["level"] == "info"
    assert "timestamp" in record


def test_configure_logging_creates_parent_directories(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "deeper" / "stalbot.log"
    configure_logging(log_level="INFO", log_file=log_file)
    assert log_file.parent.is_dir()
