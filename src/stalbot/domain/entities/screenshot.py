"""Screenshot-recognition DTOs — groundwork for OCR (PLAN.md §11.8, decision A7).

Pure domain data with zero I/O. Recognition itself does not run in v1.0
(`infrastructure.ocr.null.NullOcrGateway` always returns a disabled
`OcrResult`), but the ticket flow already handles this exact shape from day
one, so plugging in a real engine at M13 touches no calling code.
"""

from dataclasses import dataclass
from typing import Final

#: INFRA2-8: single source of truth for the "OCR not implemented yet"
#: status — was previously duplicated as two independently-defined string
#: constants (`infrastructure/ocr/null.py`, `application/services/tickets.py`),
#: a repeated literal that signaled a missing shared abstraction rather than
#: two things that happened to coincide.
OCR_STATUS_DISABLED: Final = "disabled"


@dataclass(frozen=True, slots=True)
class ScreenshotImage:
    """Raw bytes of one screenshot attachment, plus what's known about it."""

    data: bytes
    filename: str
    mime: str


@dataclass(frozen=True, slots=True)
class RecognizedItem:
    """One item OCR believes it found in a screenshot (implemented in M13)."""

    item_id: int | None
    name: str
    quantity: int
    confidence: float


@dataclass(frozen=True, slots=True)
class OcrResult:
    """What `OcrGateway.recognize()` returned for one screenshot.

    `status` mirrors `screenshot_analyses.status` (PLAN.md §8.1):
    `"disabled"` (v1.0, always) | `"pending"` | `"done"` | `"failed"`.
    """

    status: str
    raw_text: str | None = None
    items: tuple[RecognizedItem, ...] = ()
    total_estimate: str | None = None
    """`Decimal` serialized as a string, mirroring the cache column."""
    confidence: float | None = None
    duration_ms: int | None = None
    error: str | None = None
