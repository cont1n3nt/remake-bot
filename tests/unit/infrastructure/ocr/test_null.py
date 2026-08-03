"""Tests for `stalbot.infrastructure.ocr.null.NullOcrGateway` (PLAN.md §11.8, decision A7)."""

from stalbot.domain.entities.screenshot import ScreenshotImage
from stalbot.infrastructure.ocr.null import NullOcrGateway


def test_enabled_is_always_false() -> None:
    assert NullOcrGateway().enabled is False


async def test_recognize_returns_a_disabled_result() -> None:
    gateway = NullOcrGateway()
    image = ScreenshotImage(data=b"\x89PNG", filename="screenshot.png", mime="image/png")

    result = await gateway.recognize(image)

    assert result.status == "disabled"
    assert result.items == ()
    assert result.raw_text is None
