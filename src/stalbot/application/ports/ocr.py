"""Port for optical recognition of ticket screenshots (PLAN.md §3.1, §11.8).

v1.0 always runs `infrastructure.ocr.null.NullOcrGateway`. Tickets already
call this port on every screenshot (decision A7: no `if OCR_ENABLED` branch
in the ticket flow itself), so wiring in a real engine at M13 is a one-line
change in `bootstrap.py`, not a new code path.
"""

from typing import Protocol

from stalbot.domain.entities.screenshot import OcrResult, ScreenshotImage


class OcrGateway(Protocol):
    """Recognizes items in a ticket screenshot."""

    @property
    def enabled(self) -> bool:
        """Whether this gateway actually performs recognition."""
        ...

    async def recognize(self, image: ScreenshotImage) -> OcrResult:
        """Attempt to recognize items in *image*.

        Args:
            image: The screenshot to analyze.

        Returns:
            An `OcrResult`. A `status` of `"disabled"` or `"failed"` is a
            normal, non-exceptional outcome the caller must handle
            gracefully — recognition never blocks ticket confirmation
            (PLAN.md §11.8).
        """
        ...
