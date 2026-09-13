"""Tests for `infrastructure.posters.icon_store.PosterIconStore` (заявка 13.09.2026 п.6)."""

import io
from pathlib import Path

import pytest
from PIL import Image

from stalbot.domain.errors import IconRejectedError
from stalbot.infrastructure.posters.icon_store import MAX_STORED_SIZE, PosterIconStore


def _png(size: tuple[int, int] = (64, 64), color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_save_writes_the_file_and_returns_its_bare_name(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)

    filename = store.save(_png(), name_norm="морфин")

    assert "/" not in filename and "\\" not in filename
    assert (tmp_path / filename).is_file()
    assert store.exists(filename)


def test_filename_carries_the_item_name_for_readability(tmp_path: Path) -> None:
    filename = PosterIconStore(tmp_path).save(_png(), name_norm="морфин")

    assert filename.startswith("морфин-")
    assert filename.endswith(".png")


def test_two_items_never_share_a_file_even_with_identical_pictures(tmp_path: Path) -> None:
    """The «Морфин»/«мякоть лимонника» bug, prevented structurally.

    The JSON-era extractor deduplicated by content hash, so identical bytes
    meant one shared filename and the second item silently inherited the
    first one's name. Here the name is part of the filename, so the same
    picture uploaded for two items produces two files.
    """
    store = PosterIconStore(tmp_path)
    picture = _png()

    first = store.save(picture, name_norm="морфин")
    second = store.save(picture, name_norm="мякоть лимонника")

    assert first != second
    assert store.exists(first)
    assert store.exists(second)


def test_the_same_upload_for_the_same_item_is_stable(tmp_path: Path) -> None:
    """Re-uploading the identical picture must not litter the directory."""
    store = PosterIconStore(tmp_path)
    picture = _png()

    first = store.save(picture, name_norm="морфин")
    second = store.save(picture, name_norm="морфин")

    assert first == second
    assert len(list(tmp_path.iterdir())) == 1


def test_different_pictures_for_one_item_get_different_files(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)

    first = store.save(_png(color="red"), name_norm="морфин")
    second = store.save(_png(color="blue"), name_norm="морфин")

    assert first != second


def test_oversized_pictures_are_downscaled(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)

    filename = store.save(_png(size=(2000, 1200)), name_norm="морфин")

    with Image.open(tmp_path / filename) as stored:
        assert max(stored.size) == MAX_STORED_SIZE


def test_stored_icons_always_have_an_alpha_channel(tmp_path: Path) -> None:
    """`PillowRenderer` crops each icon to its alpha bounding box — a JPEG has none."""
    store = PosterIconStore(tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, format="JPEG")

    filename = store.save(buffer.getvalue(), name_norm="морфин")

    with Image.open(tmp_path / filename) as stored:
        assert stored.mode == "RGBA"


def test_a_non_image_is_rejected(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)

    with pytest.raises(IconRejectedError):
        store.save(b"definitely not a png", name_norm="морфин")


def test_an_oversized_upload_is_rejected_before_decoding(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)

    with pytest.raises(IconRejectedError):
        store.save(b"x" * (9 * 1024 * 1024), name_norm="морфин")


def test_a_name_with_path_characters_cannot_escape_the_directory(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)

    filename = store.save(_png(), name_norm="../../etc/passwd")

    assert "/" not in filename and "\\" not in filename
    assert (tmp_path / filename).is_file()


def test_the_directory_is_created_on_first_save(tmp_path: Path) -> None:
    target = tmp_path / "does" / "not" / "exist"
    store = PosterIconStore(target)

    filename = store.save(_png(), name_norm="морфин")

    assert (target / filename).is_file()


def test_delete_removes_an_unused_icon(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)
    filename = store.save(_png(), name_norm="морфин")

    assert store.delete_if_unused(filename, still_used=set()) is True
    assert not store.exists(filename)


def test_delete_keeps_an_icon_another_slot_still_points_at(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)
    filename = store.save(_png(), name_norm="морфин")

    assert store.delete_if_unused(filename, still_used={filename}) is False
    assert store.exists(filename)


def test_deleting_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    store = PosterIconStore(tmp_path)

    assert store.delete_if_unused("nothing.png", still_used=set()) is False
