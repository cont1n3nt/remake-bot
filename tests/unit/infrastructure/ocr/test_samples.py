"""Tests for `stalbot.infrastructure.ocr.samples.save_sample` (PLAN.md §11.8)."""

from pathlib import Path

from stalbot.infrastructure.ocr.samples import save_sample


async def test_writes_the_file_under_sha256_named_path(tmp_path: Path) -> None:
    samples_dir = tmp_path / "ocr_samples"

    path = await save_sample(samples_dir, "abc123", b"fake-bytes", extension="png")

    assert path == samples_dir / "abc123.png"
    assert path.read_bytes() == b"fake-bytes"


async def test_creates_the_directory_if_missing(tmp_path: Path) -> None:
    samples_dir = tmp_path / "nested" / "ocr_samples"

    await save_sample(samples_dir, "sha", b"data", extension="jpg")

    assert samples_dir.is_dir()


async def test_reusing_the_same_sha_overwrites_the_same_file(tmp_path: Path) -> None:
    samples_dir = tmp_path / "ocr_samples"

    first = await save_sample(samples_dir, "dup", b"first", extension="png")
    second = await save_sample(samples_dir, "dup", b"second", extension="png")

    assert first == second
    assert first.read_bytes() == b"second"
