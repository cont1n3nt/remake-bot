"""Tests for `stalbot.application.services.screenshots.ScreenshotService` (PLAN.md §11.5, §11.8)."""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from stalbot.application.ports.ocr import OcrGateway
from stalbot.application.services.screenshots import ScreenshotService
from stalbot.domain.entities.screenshot import OcrResult
from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)

_DATA = b"fake-screenshot-bytes"


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _fake_analyses() -> MagicMock:
    repo = MagicMock(spec=ScreenshotAnalysesRepository)
    repo.record = AsyncMock()
    repo.record_confirmed_amount = AsyncMock()
    return repo


def _fake_ocr(*, status: str = "disabled") -> MagicMock:
    gateway = MagicMock(spec=OcrGateway)
    gateway.recognize = AsyncMock(return_value=OcrResult(status=status))
    return gateway


def _settings(*, ocr_keep_samples: bool, samples_dir: Path) -> MagicMock:
    return MagicMock(ocr_keep_samples=ocr_keep_samples, ocr_samples_dir=samples_dir)


async def test_on_attached_records_the_hash_and_size(tmp_path: Path) -> None:
    analyses = _fake_analyses()
    service = ScreenshotService(
        analyses,
        _fake_ocr(),
        _settings(ocr_keep_samples=False, samples_dir=tmp_path),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.on_attached(
        111, _DATA, filename="screenshot.png", mime="image/png", image_url="https://cdn/x.png"
    )

    analyses.record.assert_awaited_once()
    _, kwargs = analyses.record.call_args
    assert kwargs["channel_id"] == 111
    assert kwargs["sha256"] == hashlib.sha256(_DATA).hexdigest()
    assert kwargs["size_bytes"] == len(_DATA)
    assert kwargs["mime"] == "image/png"
    assert kwargs["status"] == "disabled"
    assert kwargs["image_url"] == "https://cdn/x.png"
    assert kwargs["sample_path"] is None


async def test_on_attached_calls_the_ocr_gateway() -> None:
    ocr = _fake_ocr()
    service = ScreenshotService(
        _fake_analyses(),
        ocr,
        _settings(ocr_keep_samples=False, samples_dir=Path(".")),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    result = await service.on_attached(
        111, _DATA, filename="screenshot.png", mime="image/png", image_url=None
    )

    ocr.recognize.assert_awaited_once()
    assert result.status == "disabled"


async def test_on_attached_keeps_a_sample_when_enabled(tmp_path: Path) -> None:
    service = ScreenshotService(
        _fake_analyses(),
        _fake_ocr(),
        _settings(ocr_keep_samples=True, samples_dir=tmp_path),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.on_attached(
        111, _DATA, filename="screenshot.jpg", mime="image/jpeg", image_url=None
    )

    sha = hashlib.sha256(_DATA).hexdigest()
    saved = tmp_path / f"{sha}.jpg"
    assert saved.read_bytes() == _DATA


async def test_on_attached_skips_the_sample_when_disabled(tmp_path: Path) -> None:
    analyses = _fake_analyses()
    service = ScreenshotService(
        analyses,
        _fake_ocr(),
        _settings(ocr_keep_samples=False, samples_dir=tmp_path),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.on_attached(
        111, _DATA, filename="screenshot.png", mime="image/png", image_url=None
    )

    assert list(tmp_path.glob("*")) == []
    assert analyses.record.call_args.kwargs["sample_path"] is None


async def test_record_confirmed_amount_forwards_the_stringified_decimal(tmp_path: Path) -> None:
    analyses = _fake_analyses()
    service = ScreenshotService(
        analyses,
        _fake_ocr(),
        _settings(ocr_keep_samples=False, samples_dir=tmp_path),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.record_confirmed_amount(111, Decimal(299900))

    analyses.record_confirmed_amount.assert_awaited_once_with(111, "299900")
