"""Tests for `stalbot.infrastructure.posters.pillow_renderer.PillowRenderer` (Часть IX, Э11).

Points 2, 4, 5 of Часть IX's five-point poster test list (point 1 lives in
`tests/unit/application/services/test_posters.py`, point 3 is the
`_truncate_to_width` test below).
"""

import io
from pathlib import Path

from PIL import Image, ImageFont

from stalbot.application.dto.poster_spec import PosterSection, PosterSlot, PosterSpec
from stalbot.infrastructure.posters.pillow_renderer import PillowRenderer, _px, _truncate_to_width

_ASSETS = Path("src/stalbot/assets/posters")
_ICON = _ASSETS / "icons" / "m18 claymore.png"
_LOGO = _ASSETS / "logo.png"
_FONT = Path("src/stalbot/assets/fonts/ComicRelief-Regular.ttf")


def _slot(name: str = "Тестовый предмет", price_text: str = "1 000 р.") -> PosterSlot:
    return PosterSlot(name=name, price_text=price_text, icon_path=_ICON)


def _spec(
    *,
    price_text: str = "1 000 р.",
    blocks_per_row: int = 3,
    logo_position: int | None = None,
    logo_width: int = 2,
) -> PosterSpec:
    sections = (PosterSection(name=None, slots=(_slot(price_text=price_text),)),)
    return PosterSpec(
        title="Тестовый плакат",
        logo_path=_LOGO,
        sections=sections,
        blocks_per_row=blocks_per_row,
        logo_position=logo_position if logo_position is not None else len(sections) // 2,
        logo_width=logo_width,
    )


def _named_sections_spec(
    section_count: int,
    *,
    blocks_per_row: int = 3,
    logo_position: int | None = None,
    logo_width: int = 2,
) -> PosterSpec:
    sections = tuple(
        PosterSection(name=f"Секция {i}", slots=(_slot(),)) for i in range(section_count)
    )
    return PosterSpec(
        title="Тестовый плакат",
        logo_path=_LOGO,
        sections=sections,
        blocks_per_row=blocks_per_row,
        logo_position=logo_position if logo_position is not None else len(sections) // 2,
        logo_width=logo_width,
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


def test_render_canvas_width_is_governed_by_spec_blocks_per_row() -> None:
    """`PosterSpec.blocks_per_row` (set per poster kind by `PosterService`,
    e.g. 10 for resources, 3 for boosts/boost_purchases) drives the canvas
    width directly — every block is a single narrow card column."""
    image = Image.open(io.BytesIO(PillowRenderer().render(_spec(blocks_per_row=10))))

    card_width, block_gap, padding = _px(300), _px(20), _px(24)
    blocks_per_row = 10
    expected_width = blocks_per_row * card_width + (blocks_per_row - 1) * block_gap + 2 * padding
    assert image.width == expected_width


def test_render_wraps_sections_onto_a_new_row_past_blocks_per_row() -> None:
    """A poster with more sections than `blocks_per_row` wraps onto a second
    block row instead of widening the canvas further."""
    three = Image.open(io.BytesIO(PillowRenderer().render(_named_sections_spec(3))))
    four = Image.open(io.BytesIO(PillowRenderer().render(_named_sections_spec(4))))

    assert three.width == four.width
    assert four.height > three.height


def test_render_packs_the_logo_inline_as_a_double_width_block() -> None:
    """The logo is packed like an ordinary (but twice as wide) block among
    the sections (bug report, Часть IX, Э11) rather than floated on top of
    a card or given a whole row of its own. At blocks_per_row=3, inserting
    a width-2 logo among 3 width-1 sections pushes one section onto a
    second row: row 1 = [section 0, logo] (width 1+2=3), row 2 = the
    remaining two sections."""
    image = Image.open(io.BytesIO(PillowRenderer().render(_named_sections_spec(3))))

    title_height, padding, section_gap = _px(64), _px(24), _px(20)
    header_height, header_gap, card_height = _px(40), _px(10), _px(56)
    row_height = header_height + header_gap + card_height
    assert image.height == title_height + padding + 2 * row_height + section_gap + padding


def test_render_packs_the_logo_between_four_and_four_sections_when_it_fits() -> None:
    """Matches the reference sheet's own layout: N sections, the logo at a
    normal visible size, N more sections — all in a single row once they
    fit within blocks_per_row, instead of forcing a separate row."""
    spec = _named_sections_spec(8, blocks_per_row=10)

    image = Image.open(io.BytesIO(PillowRenderer().render(spec)))

    title_height, padding = _px(64), _px(24)
    header_height, header_gap, card_height = _px(40), _px(10), _px(56)
    single_row_height = header_height + header_gap + card_height
    assert image.height == title_height + padding + single_row_height + padding


def test_render_a_multi_column_section_does_not_wrap_within_its_row() -> None:
    """A section with `columns=2` (e.g. boosts' «Медицина», bug report
    Часть IX, Э11) uses up 2 of `blocks_per_row`'s column-budget by itself
    — at blocks_per_row=2 it fills the row alone, pushing anything else
    (here, the logo) onto a second row rather than squeezing beside it."""
    wide_section = PosterSection(
        name="Медицина", slots=tuple(_slot() for _ in range(12)), columns=2
    )
    spec = PosterSpec(
        title="Тестовый плакат",
        logo_path=_LOGO,
        sections=(wide_section,),
        blocks_per_row=2,
        logo_position=1,
        logo_width=1,
    )

    image = Image.open(io.BytesIO(PillowRenderer().render(spec)))

    card_width, block_gap, padding = _px(300), _px(20), _px(24)
    expected_width = 2 * card_width + block_gap + 2 * padding
    assert image.width == expected_width

    header_height, header_gap, card_height, card_gap = _px(40), _px(10), _px(56), _px(10)
    section_row_height = (
        header_height + header_gap + 6 * card_height + 5 * card_gap
    )  # 12 slots / 2 cols
    logo_row_height = _px(120)
    title_height, section_gap = _px(64), _px(20)
    expected_height = (
        title_height + padding + section_row_height + section_gap + logo_row_height + padding
    )
    assert image.height == expected_height


def test_render_a_multi_column_section_is_shorter_than_a_single_column_would_be() -> None:
    """12 slots split 2 columns wide (6 rows) must be shorter than the same
    12 slots forced into 1 column (12 rows) — proving the section's
    `columns` actually changes the card grid, not just the header width."""
    two_col = PosterSection(name="С", slots=tuple(_slot() for _ in range(12)), columns=2)
    one_col = PosterSection(name="С", slots=tuple(_slot() for _ in range(12)), columns=1)

    def _render_height(section: PosterSection) -> int:
        spec = PosterSpec(
            title="Т",
            logo_path=_LOGO,
            sections=(section,),
            blocks_per_row=2,
            logo_position=1,
            logo_width=1,
        )
        image = Image.open(io.BytesIO(PillowRenderer().render(spec)))
        return image.height

    assert _render_height(two_col) < _render_height(one_col)
