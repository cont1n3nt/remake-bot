"""Tests for `stalbot.infrastructure.posters.pillow_renderer.PillowRenderer` (Часть IX, Э11).

Points 2, 4, 5 of Часть IX's five-point poster test list (point 1 lives in
`tests/unit/application/services/test_posters.py`, point 3 is the
`_truncate_to_width` test below).
"""

import io
from pathlib import Path

from PIL import Image, ImageFont

from stalbot.application.dto.poster_spec import PosterSection, PosterSlot, PosterSpec
from stalbot.infrastructure.posters.pillow_renderer import PillowRenderer, _truncate_to_width

_ASSETS = Path("src/stalbot/assets/posters")
_ICON = _ASSETS / "icons" / "m18 claymore.png"
_LOGO = _ASSETS / "logo.png"
_FONT = Path("src/stalbot/assets/fonts/ComicRelief-Regular.ttf")


def _spec(*, price_text: str = "1 000 р.") -> PosterSpec:
    return PosterSpec(
        title="Тестовый плакат",
        logo_path=_LOGO,
        sections=(
            PosterSection(
                name=None,
                slots=(
                    PosterSlot(name="Тестовый предмет", price_text=price_text, icon_path=_ICON),
                ),
            ),
        ),
    )


def test_render_produces_a_valid_png_with_sane_dimensions() -> None:
    png = PillowRenderer().render(_spec())

    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.width > 0
    assert image.height > 0


def test_render_is_deterministic_for_the_same_spec() -> None:
    """Two renders of an identical spec must be byte-for-byte identical —
    no PNG timestamp, no non-deterministic ordering."""
    spec = _spec()

    first = PillowRenderer().render(spec)
    second = PillowRenderer().render(spec)

    assert first == second


def test_render_changes_when_a_slot_s_price_changes() -> None:
    """The rendered bytes must actually reflect the input — proves this
    isn't accidentally caching/reusing a stale image across calls."""
    baseline = PillowRenderer().render(_spec(price_text="1 000 р."))
    changed = PillowRenderer().render(_spec(price_text="999 999 999 р."))

    assert baseline != changed


def test_truncate_to_width_never_exceeds_the_given_width() -> None:
    """The longest realistic name + the largest realistic price must both
    fit their card — `_truncate_to_width` is what guarantees this instead
    of letting Pillow silently overflow the card border."""
    font = ImageFont.truetype(str(_FONT), 16)
    max_width = 200

    for text in (
        "Очень длинное название предмета, которое точно не влезет в карточку целиком",
        "999 999 999 р.",
    ):
        truncated = _truncate_to_width(text, font, max_width)
        bbox = font.getbbox(truncated)
        assert bbox[2] - bbox[0] <= max_width


def test_truncate_to_width_leaves_short_text_untouched() -> None:
    font = ImageFont.truetype(str(_FONT), 16)

    assert _truncate_to_width("Уха", font, 200) == "Уха"
