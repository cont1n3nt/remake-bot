"""Port for storing poster icon files (заявка 13.09.2026 п.6).

`PosterLayoutService` decides *when* an icon is saved or removed; it must
not know that doing so means decoding a PNG with Pillow, which
`tests/unit/test_architecture_invariants.py` confines to
`infrastructure/posters/` anyway.
"""

from typing import Protocol


class IconStore(Protocol):
    """Saves, checks and removes poster icon files."""

    def save(self, data: bytes, *, name_norm: str) -> str:
        """Store *data* as an icon and return its bare filename.

        Args:
            data: Raw uploaded bytes.
            name_norm: The item's normalized name, for a readable filename.

        Raises:
            IconRejectedError: The upload is too large or not an image.
        """
        ...

    def exists(self, filename: str) -> bool:
        """Whether an icon file is actually on disk.

        Args:
            filename: Bare filename as stored on the slot.
        """
        ...

    def delete_if_unused(self, filename: str, *, still_used: set[str]) -> bool:
        """Remove an icon file unless another slot still points at it.

        Args:
            filename: Bare filename to remove.
            still_used: Every filename any slot references right now.

        Returns:
            Whether the file was actually removed.
        """
        ...
