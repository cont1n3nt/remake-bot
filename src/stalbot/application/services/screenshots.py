"""Screenshot intake for tickets: hashing, dataset collection, OCR call (PLAN.md §11.5, §11.8).

`ScreenshotService.on_attached()` is the one place a ticket's screenshot
bytes get processed — it always calls `OcrGateway.recognize()` (decision A7:
no `if OCR_ENABLED` branch in the ticket flow itself), always records a
`screenshot_analyses` row, and — if `OCR_KEEP_SAMPLES` is on — always keeps
a copy for the future training dataset, regardless of whether OCR is enabled.
"""

import hashlib
import logging
from decimal import Decimal
from typing import Final

from stalbot.application.ports.clock import Clock
from stalbot.application.ports.ocr import OcrGateway
from stalbot.config.settings import Settings
from stalbot.domain.entities.screenshot import OcrResult, ScreenshotImage
from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)
from stalbot.infrastructure.ocr.samples import save_sample

logger = logging.getLogger(__name__)

_DEFAULT_EXTENSION = "png"
_FAILED_STATUS = "failed"

#: INFRA2-5: the attachment's `mime` (Discord's own reported content type) is
#: a more authoritative source for the sample's real format than its
#: filename — a screenshot renamed by the uploader's OS keeps whatever
#: extension it had before, regardless of what the bytes actually are. Used
#: as the primary source; an unrecognized mime falls back to the filename's
#: own extension (today's behavior), not an error — dataset quality, not a
#: guarantee anything depends on.
_MIME_EXTENSIONS: Final[dict[str, str]] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
}


def _sample_extension(*, mime: str, filename: str) -> str:
    # `content_type` can carry a `; charset=...`/`; boundary=...` parameter
    # after the bare type (discord.py passes `Attachment.content_type`
    # through verbatim) — normalized the same way `tickets/cog.py`'s
    # `_first_image_attachment` already normalizes the same value, or a
    # parameterized mime would always miss this dict and silently fall back
    # to the filename-derived extension, quietly reintroducing INFRA2-5.
    bare_mime = mime.split(";", 1)[0].strip().lower()
    known = _MIME_EXTENSIONS.get(bare_mime)
    if known is not None:
        return known
    return filename.rsplit(".", 1)[-1] if "." in filename else _DEFAULT_EXTENSION


class ScreenshotService:
    """Handles a screenshot attachment the moment it lands in a ticket channel."""

    def __init__(
        self,
        analyses: ScreenshotAnalysesRepository,
        ocr: OcrGateway,
        settings: Settings,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            analyses: Cache repository for `screenshot_analyses`.
            ocr: Recognition port; `NullOcrGateway` in v1.0.
            settings: For `ocr_keep_samples`/`ocr_samples_dir`.
            clock: Time source, tz-aware `GMT3`.
        """
        self._analyses = analyses
        self._ocr = ocr
        self._settings = settings
        self._clock = clock

    async def on_attached(
        self, channel_id: int, data: bytes, *, filename: str, mime: str, image_url: str | None
    ) -> OcrResult:
        """Process one screenshot: hash, dataset copy, OCR call, bookkeeping row.

        Args:
            channel_id: Ticket channel the screenshot was attached in.
            data: Original, unmodified screenshot bytes.
            filename: Original attachment filename (used for its extension).
            mime: Content type, e.g. `"image/png"`.
            image_url: The log-channel's permanent CDN URL, once known.

        Returns:
            Whatever `OcrGateway.recognize()` returned — `status="disabled"`
            in v1.0.
        """
        sha256 = hashlib.sha256(data).hexdigest()

        sample_path: str | None = None
        if self._settings.ocr_keep_samples:
            extension = _sample_extension(mime=mime, filename=filename)
            path = await save_sample(
                self._settings.ocr_samples_dir, sha256, data, extension=extension
            )
            sample_path = str(path)

        image = ScreenshotImage(data=data, filename=filename, mime=mime)
        try:
            result = await self._ocr.recognize(image)
        except Exception as exc:
            # OCR must never block ticket confirmation (PLAN.md §11.8) — the
            # contract is enforced only by convention today (`NullOcrGateway`
            # never raises), not at this call site (APP-8). A future real
            # engine failing mid-recognition must degrade the same way a
            # clean "failed" result already does, not propagate. Deliberately
            # `Exception`, not narrower: any recognition failure must degrade,
            # while `asyncio.CancelledError` (a `BaseException`) still
            # propagates normally, which is what we want on shutdown/timeout.
            logger.warning(
                "OCR recognition failed for channel %d: %s", channel_id, exc, exc_info=exc
            )
            result = OcrResult(status=_FAILED_STATUS, error=str(exc))

        await self._analyses.record(
            channel_id=channel_id,
            sha256=sha256,
            image_url=image_url,
            sample_path=sample_path,
            size_bytes=len(data),
            mime=mime,
            status=result.status,
            created_at=self._clock.now().isoformat(),
        )
        return result

    async def record_confirmed_amount(self, channel_id: int, amount: Decimal) -> None:
        """Label this ticket's screenshot(s) with the admin-confirmed deal amount.

        A future OCR training pair — screenshot in, correct total out
        (PLAN.md §11.8). Harmless no-op if the ticket had no screenshot.

        Args:
            channel_id: The confirmed ticket's channel.
            amount: The confirmed deal amount.
        """
        await self._analyses.record_confirmed_amount(channel_id, str(amount))
