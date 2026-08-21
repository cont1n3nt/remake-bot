"""`PillowRenderer` — the only module in this codebase that imports PIL (Часть IX, Э11).

There is no ready-made background/coordinate map (owner confirmed
2026-08-15 — the old "poster" sheets were just the live spreadsheet,
screenshotted by hand). This draws an equivalent-style poster from
scratch every time: colored card grid, section headers, logo — computed
from however many slots `PosterSpec` actually has, not read off fixed
pixel coordinates. Deliberately not a pixel-exact clone of the old
screenshots (owner-approved trade-off).
"""

import io
from dataclasses import dataclass
from importlib import resources
from itertools import cycle
from pathlib import Path
from typing import Final

from PIL import Image, ImageChops, ImageDraw, ImageFont

from stalbot.application.dto.poster_spec import PosterSection, PosterSlot, PosterSpec

#: Every pixel size below is defined in "base" units and scaled by this
#: factor (bug report, Часть IX, Э11 — Discord shrinks a wide poster into a
#: small chat thumbnail, and phone screens shrink it further; the original
#: 1x sizing left card text too small to read once scaled down). Bump this
#: single constant to tune overall render resolution/legibility.
_SCALE: Final = 1.5


def _px(base: float) -> int:
    return round(base * _SCALE)


_CANVAS_PADDING: Final = _px(24)
_TITLE_HEIGHT: Final = _px(64)
_SECTION_HEADER_HEIGHT: Final = _px(40)
_SECTION_HEADER_GAP: Final = _px(10)
_SECTION_GAP: Final = _px(20)
_CARD_WIDTH: Final = _px(300)
_CARD_HEIGHT: Final = _px(56)
_CARD_GAP: Final = _px(10)
_ICON_SIZE: Final = _px(44)
_ICON_PADDING: Final = _px(6)
_ICON_RADIUS: Final = _px(8)
_LOGO_SIZE: Final = _px(120)
_LOGO_RADIUS: Final = _px(16)

#: Every section (named or not) renders as a narrow one-column block —
#: `PosterSpec.blocks_per_row` many side by side per row, wrapping to a new
#: row past that (bug report, Часть IX, Э11), echoing the source sheet's
#: side-by-side columns instead of one long vertical stack.
_BLOCK_COLUMNS: Final = 1
_BLOCK_GAP: Final = _px(20)


@dataclass(frozen=True, slots=True)
class _Logo:
    """Sentinel block standing in for the logo when packing rows.

    Packed inline like an ordinary section, just `width` block-columns wide
    (kind-specific — `PosterSpec.logo_width`, bug report Часть IX, Э11)
    instead of shrunk into a corner or given a whole reserved row.
    """

    width: int


_BACKGROUND: Final = (255, 246, 224, 255)
_TITLE_BG: Final = (255, 214, 130, 255)
_TITLE_TEXT: Final = (60, 40, 10, 255)
_CARD_BG: Final = (255, 255, 255, 255)
_CARD_TEXT: Final = (30, 30, 30, 255)
_PRICE_TEXT: Final = (20, 110, 40, 255)
_SECTION_TEXT: Final = (30, 30, 30, 255)
#: Cycled per section so consecutive sections read as visually distinct
#: groups, echoing the source sheet's per-block colored frames.
_SECTION_PALETTE: Final = (
    (159, 197, 232, 255),  # blue
    (234, 153, 153, 255),  # pink/red
    (182, 215, 168, 255),  # green
    (255, 217, 102, 255),  # yellow
)


@dataclass(frozen=True, slots=True)
class _Fonts:
    title: ImageFont.FreeTypeFont
    section: ImageFont.FreeTypeFont
    name: ImageFont.FreeTypeFont
    price: ImageFont.FreeTypeFont


def _fonts_dir() -> Path:
    return Path(str(resources.files("stalbot") / "assets" / "fonts"))


def _load_fonts() -> _Fonts:
    regular = _fonts_dir() / "ComicRelief-Regular.ttf"
    bold = _fonts_dir() / "ComicRelief-Bold.ttf"
    return _Fonts(
        title=ImageFont.truetype(str(bold), _px(32)),
        section=ImageFont.truetype(str(bold), _px(22)),
        name=ImageFont.truetype(str(regular), _px(16)),
        price=ImageFont.truetype(str(bold), _px(16)),
    )


def _round_corners(image: Image.Image, radius: int) -> Image.Image:
    """Clip *image* (RGBA) to a rounded-rectangle mask.

    Intersected with its existing alpha so an already-transparent source
    stays transparent.
    """
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.width - 1, image.height - 1), radius=radius, fill=255
    )
    rounded = image.copy()
    rounded.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    return rounded


def _fit_icon(icon_path: Path, size: int) -> Image.Image:
    icon = Image.open(icon_path).convert("RGBA")
    # Source icons carry wildly different amounts of baked-in transparent
    # padding (extracted from many different cells across the workbook,
    # Часть IX) — fitting the raw canvas made some items (e.g. Аммиак) look
    # tiny next to tightly-cropped ones. Crop to the alpha channel's real
    # bounding box first so every icon fills its slot the same amount.
    bbox = icon.split()[-1].getbbox()
    if bbox is not None:
        icon = icon.crop(bbox)
    # Stretched to fill the square slot exactly, portrait/landscape source
    # crops included (bug report, Часть IX, Э11) — a preserve-aspect resize
    # left portrait icons narrow and small inside the box.
    icon = icon.resize((size, size), Image.Resampling.LANCZOS)
    return _round_corners(icon, _ICON_RADIUS)


def _truncate_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if font.getbbox(text)[2] <= max_width:
        return text
    ellipsis = "…"
    truncated = text
    while truncated and font.getbbox(truncated + ellipsis)[2] > max_width:
        truncated = truncated[:-1]
    return truncated + ellipsis if truncated else ellipsis


def _section_rows(slot_count: int, columns: int) -> int:
    return -(-slot_count // columns)  # ceil division


def _section_height(slot_count: int, columns: int, *, has_name: bool) -> int:
    rows = _section_rows(slot_count, columns)
    height = rows * _CARD_HEIGHT + max(rows - 1, 0) * _CARD_GAP
    if has_name:
        height += _SECTION_HEADER_HEIGHT + _SECTION_HEADER_GAP
    return height


def _pack_rows(
    sections: tuple[PosterSection, ...],
    blocks_per_row: int,
    logo_position: int,
    logo_width: int,
) -> list[list[PosterSection | _Logo]]:
    """Greedily pack *sections* plus one `_Logo` into rows of `blocks_per_row`.

    Sections are width 1, the logo `logo_width` — both `PosterSpec` fields
    set per poster kind, so each poster can match its own reference layout
    (e.g. resources: 4 sections, logo, 4 sections; boosts: the logo as a
    single normal-width column right before the last section) rather than
    consuming a whole row of its own.
    """
    cells: list[PosterSection | _Logo] = list(sections)
    cells.insert(logo_position, _Logo(width=logo_width))

    rows: list[list[PosterSection | _Logo]] = []
    current: list[PosterSection | _Logo] = []
    used = 0
    for cell in cells:
        width = cell.width if isinstance(cell, _Logo) else cell.columns
        if current and used + width > blocks_per_row:
            rows.append(current)
            current = []
            used = 0
        current.append(cell)
        used += width
    if current:
        rows.append(current)
    return rows


def _cell_width_px(width_units: int) -> int:
    block_width = _BLOCK_COLUMNS * _CARD_WIDTH + (_BLOCK_COLUMNS - 1) * _CARD_GAP
    return width_units * block_width + (width_units - 1) * _BLOCK_GAP


class PillowRenderer:
    """`PosterRenderer` implementation — draws a computed card grid."""

    def render(self, spec: PosterSpec) -> bytes:
        """Draw *spec* and return PNG bytes (see `PosterRenderer.render`)."""
        fonts = _load_fonts()
        blocks_per_row = spec.blocks_per_row
        row_width = _cell_width_px(blocks_per_row)

        block_rows = _pack_rows(spec.sections, blocks_per_row, spec.logo_position, spec.logo_width)
        row_heights = [
            max(
                (
                    _section_height(len(cell.slots), cell.columns, has_name=cell.name is not None)
                    for cell in row
                    if isinstance(cell, PosterSection)
                ),
                default=_LOGO_SIZE,
            )
            for row in block_rows
        ]
        content_height = sum(row_heights) + max(len(block_rows) - 1, 0) * _SECTION_GAP

        canvas_width = row_width + 2 * _CANVAS_PADDING
        canvas_height = _TITLE_HEIGHT + _CANVAS_PADDING + content_height + _CANVAS_PADDING

        image = Image.new("RGBA", (canvas_width, canvas_height), _BACKGROUND)
        draw = ImageDraw.Draw(image)

        self._draw_title(draw, spec.title, canvas_width, fonts)

        y = _TITLE_HEIGHT + _CANVAS_PADDING
        palette = cycle(_SECTION_PALETTE)
        for row, row_height in zip(block_rows, row_heights, strict=True):
            x = _CANVAS_PADDING
            for cell in row:
                if isinstance(cell, _Logo):
                    cell_width = _cell_width_px(cell.width)
                    self._draw_logo(image, spec.logo_path, x, y, cell_width, row_height)
                    x += cell_width + _BLOCK_GAP
                else:
                    color = next(palette)
                    cell_width = _cell_width_px(cell.columns)
                    self._draw_section(image, draw, cell, x, y, cell.columns, fonts, color)
                    x += cell_width + _BLOCK_GAP
            y += row_height + _SECTION_GAP

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()

    def _draw_title(
        self, draw: ImageDraw.ImageDraw, title: str, canvas_width: int, fonts: _Fonts
    ) -> None:
        draw.rectangle(
            (0, 0, canvas_width, _TITLE_HEIGHT),
            fill=_TITLE_BG,
            outline=(0, 0, 0, 40),
            width=_px(2),
        )
        bbox = fonts.title.getbbox(title)
        text_width = bbox[2] - bbox[0]
        draw.text(
            ((canvas_width - text_width) / 2, _TITLE_HEIGHT / 2),
            title,
            font=fonts.title,
            fill=_TITLE_TEXT,
            anchor="lm",
        )

    def _draw_section(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        section: PosterSection,
        x: int,
        y: int,
        columns: int,
        fonts: _Fonts,
        color: tuple[int, int, int, int],
    ) -> None:
        if section.name is not None:
            block_width = columns * _CARD_WIDTH + (columns - 1) * _CARD_GAP
            draw.rounded_rectangle(
                (x, y, x + block_width, y + _SECTION_HEADER_HEIGHT), radius=_px(8), fill=color
            )
            draw.text(
                (x + _px(12), y + _SECTION_HEADER_HEIGHT / 2),
                section.name,
                font=fonts.section,
                fill=_SECTION_TEXT,
                anchor="lm",
            )
            y += _SECTION_HEADER_HEIGHT + _SECTION_HEADER_GAP

        # Column-major: the first column fills top-to-bottom before the
        # next one starts (bug report, Часть IX, Э11 — matches the
        # reference sheet's «Медицина», one header over two read-down
        # columns), not left-to-right row by row.
        rows_per_column = _section_rows(len(section.slots), columns)
        for index, slot in enumerate(section.slots):
            col = index // rows_per_column
            row = index % rows_per_column
            card_x = x + col * (_CARD_WIDTH + _CARD_GAP)
            card_y = y + row * (_CARD_HEIGHT + _CARD_GAP)
            self._draw_card(image, draw, slot, card_x, card_y, fonts, color)

    def _draw_card(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        slot: PosterSlot,
        x: int,
        y: int,
        fonts: _Fonts,
        color: tuple[int, int, int, int],
    ) -> None:
        draw.rounded_rectangle(
            (x, y, x + _CARD_WIDTH, y + _CARD_HEIGHT),
            radius=_px(8),
            fill=_CARD_BG,
            outline=color,
            width=_px(3),
        )

        icon = _fit_icon(slot.icon_path, _ICON_SIZE)
        icon_y = y + (_CARD_HEIGHT - _ICON_SIZE) // 2
        image.paste(icon, (x + _ICON_PADDING, icon_y), icon)

        text_x = x + _ICON_PADDING * 2 + _ICON_SIZE
        text_max_width = _CARD_WIDTH - (text_x - x) - _ICON_PADDING
        name = _truncate_to_width(slot.name, fonts.name, text_max_width)
        draw.text(
            (text_x, y + _CARD_HEIGHT * 0.32),
            name,
            font=fonts.name,
            fill=_CARD_TEXT,
            anchor="lm",
        )
        draw.text(
            (text_x, y + _CARD_HEIGHT * 0.7),
            slot.price_text,
            font=fonts.price,
            fill=_PRICE_TEXT,
            anchor="lm",
        )

    def _draw_logo(
        self,
        image: Image.Image,
        logo_path: Path,
        cell_x: int,
        cell_y: int,
        cell_width: int,
        cell_height: int,
    ) -> None:
        """Paste the logo centered in its own reserved, double-width block.

        No backdrop or border (bug report, Часть IX, Э11): the cell is
        empty space reserved just for it, so nothing needs covering.
        """
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
        logo = _round_corners(logo, _LOGO_RADIUS)
        x = cell_x + (cell_width - logo.width) // 2
        # Top-aligned with cell_y — level with the other blocks' section
        # headers in the same row (bug report, Часть IX, Э11), not centered.
        y = cell_y
        image.paste(logo, (x, y), logo)
