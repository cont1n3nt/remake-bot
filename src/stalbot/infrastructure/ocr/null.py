"""`NullOcrGateway` — the `OcrGateway` port's v1.0 implementation (PLAN.md §11.8, decision A7).

Always disabled; real recognition ships in M13. Tickets call this
unconditionally so the call site never needs an `if OCR_ENABLED` branch.
"""

from stalbot.domain.entities.screenshot import OcrResult, ScreenshotImage

_DISABLED_STATUS = "disabled"


class NullOcrGateway:
    """No-op `OcrGateway`: recognition is not implemented until M13."""

    @property
    def enabled(self) -> bool:
        """Always `False` in v1.0."""
        return False

    async def recognize(self, image: ScreenshotImage) -> OcrResult:
        """Return a `status="disabled"` result without doing any work.

        Args:
            image: Ignored.
        """
        return OcrResult(status=_DISABLED_STATUS)
