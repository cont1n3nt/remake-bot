"""Port for drawing a `PosterSpec` into a PNG (Часть IX, Э11)."""

from typing import Protocol

from stalbot.application.dto.poster_spec import PosterSpec


class PosterRenderer(Protocol):
    """Renders a fully-resolved poster spec to PNG bytes."""

    def render(self, spec: PosterSpec) -> bytes:
        """Draw *spec* and return the finished image as PNG bytes.

        Args:
            spec: Title, logo, and sections/slots to draw — already
                resolved (live prices, real icon paths). This call does no
                I/O of its own beyond reading the icon/font files on disk.
        """
        ...
