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

from PIL import Image, ImageDraw, ImageFont

from stalbot.application.dto.poster_spec import PosterSection, PosterSlot, PosterSpec

_CANVAS_PADDING: Final = 24
_TITLE_HEIGHT: Final = 64
_SECTION_HEADER_HEIGHT: Final = 40
_SECTION_GAP: Final = 20
_CARD_WIDTH: Final = 300
_CARD_HEIGHT: Final = 56
_CARD_GAP: Final = 10
_ICON_SIZE: Final = 44
_ICON_PADDING: Final = 6
_COLUMNS: Final = 3
_LOGO_SIZE: Final = 120

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
        title=ImageFont.truetype(str(bold), 32),
        section=ImageFont.truetype(str(bold), 22),
        name=ImageFont.truetype(str(regular), 16),
        price=ImageFont.truetype(str(bold), 16),
    )


def _fit_icon(icon_path: Path, size: int) -> Image.Image:
    icon = Image.open(icon_path).convert("RGBA")
    icon.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = ((size - icon.width) // 2, (size - icon.height) // 2)
    canvas.paste(icon, offset, icon)
    return canvas


def _truncate_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if font.getbbox(text)[2] <= max_width:
        return text
    ellipsis = "…"
    truncated = text
    while truncated and font.getbbox(truncated + ellipsis)[2] > max_width:
        truncated = truncated[:-1]
    return truncated + ellipsis if truncated else ellipsis


def _section_rows(slot_count: int) -> int:
    return -(-slot_count // _COLUMNS)  # ceil division


def _section_height(slot_count: int, *, has_name: bool) -> int:
    rows = _section_rows(slot_count)
    height = rows * _CARD_HEIGHT + max(rows - 1, 0) * _CARD_GAP
    if has_name:
        height += _SECTION_HEADER_HEIGHT
    return height


class PillowRenderer:
    """`PosterRenderer` implementation — draws a computed card grid."""

    def render(self, spec: PosterSpec) -> bytes:
        """Draw *spec* and return PNG bytes (see `PosterRenderer.render`)."""
        fonts = _load_fonts()
        grid_width = _COLUMNS * _CARD_WIDTH + (_COLUMNS - 1) * _CARD_GAP

        content_height = sum(
            _section_height(len(section.slots), has_name=section.name is not None) + _SECTION_GAP
            for section in spec.sections
        )
        canvas_width = grid_width + 2 * _CANVAS_PADDING
        canvas_height = (
            _TITLE_HEIGHT + content_height + 2 * _CANVAS_PADDING + _LOGO_SIZE + _CANVAS_PADDING
        )

        image = Image.new("RGBA", (canvas_width, canvas_height), _BACKGROUND)
        draw = ImageDraw.Draw(image)

        self._draw_title(draw, spec.title, canvas_width, fonts)

        y = _TITLE_HEIGHT + _CANVAS_PADDING
        palette = cycle(_SECTION_PALETTE)
        for section in spec.sections:
            color = next(palette)
            y = self._draw_section(image, draw, section, y, fonts, color)
            y += _SECTION_GAP

        self._draw_logo(image, spec.logo_path, canvas_width, y)

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()

    def _draw_title(
        self, draw: ImageDraw.ImageDraw, title: str, canvas_width: int, fonts: _Fonts
    ) -> None:
        draw.rectangle(
            (0, 0, canvas_width, _TITLE_HEIGHT), fill=_TITLE_BG, outline=(0, 0, 0, 40), width=2
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
        y: int,
        fonts: _Fonts,
        color: tuple[int, int, int, int],
    ) -> int:
        x = _CANVAS_PADDING
        grid_width = _COLUMNS * _CARD_WIDTH + (_COLUMNS - 1) * _CARD_GAP

        if section.name is not None:
            draw.rectangle((x, y, x + grid_width, y + _SECTION_HEADER_HEIGHT), fill=color)
            draw.text(
                (x + 12, y + _SECTION_HEADER_HEIGHT / 2),
                section.name,
                font=fonts.section,
                fill=_SECTION_TEXT,
                anchor="lm",
            )
            y += _SECTION_HEADER_HEIGHT

        for index, slot in enumerate(section.slots):
            col = index % _COLUMNS
            row = index // _COLUMNS
            card_x = x + col * (_CARD_WIDTH + _CARD_GAP)
            card_y = y + row * (_CARD_HEIGHT + _CARD_GAP)
            self._draw_card(image, draw, slot, card_x, card_y, fonts, color)

        return y + _section_rows(len(section.slots)) * (_CARD_HEIGHT + _CARD_GAP) - _CARD_GAP

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
            radius=8,
            fill=_CARD_BG,
            outline=color,
            width=3,
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

    def _draw_logo(self, image: Image.Image, logo_path: Path, canvas_width: int, y: int) -> None:
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((_LOGO_SIZE, _LOGO_SIZE), Image.Resampling.LANCZOS)
        x = (canvas_width - logo.width) // 2
        image.paste(logo, (x, y + _CANVAS_PADDING // 2), logo)
