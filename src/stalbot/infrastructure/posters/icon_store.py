"""Where uploaded poster icons are kept (заявка 13.09.2026 п.6).

The only place that turns an uploaded screenshot into a poster icon file.
Lives here, not in `application/`, because it is the second module allowed
to import PIL (`tests/unit/test_architecture_invariants.py`).

Filenames are `<name>-<content hash>.png`, and that is a fix, not a
decoration: the JSON-era extractor named icons after the item alone and
deduplicated by content hash, so two items whose extracted images happened
to match ended up sharing one filename — which is how «Морфин» came to be
drawn with мякоть лимонника's picture on «Скуп ваших бустов». With the hash
in the name, two different pictures can never collide onto one file, and
the same picture uploaded twice for two items gets two files rather than
one shared one.
"""

import hashlib
import io
import re
from pathlib import Path
from typing import Final

from PIL import Image, UnidentifiedImageError

from stalbot.domain.errors import IconRejectedError

#: Longest side an icon is stored at. `PillowRenderer` draws them at 44 px
#: (at 1x), so anything past this is bytes on disk that never reach a
#: pixel — but keep enough headroom for a future higher-resolution poster.
MAX_STORED_SIZE: Final = 512

#: Largest upload accepted, before decoding. A poster icon is a cropped
#: screenshot of one item; anything bigger is a mistake, and decoding it
#: would be the expensive way to find that out.
MAX_UPLOAD_BYTES: Final = 8 * 1024 * 1024

_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
_HASH_LENGTH: Final = 12


class PosterIconStore:
    """Saves, finds and removes poster icon files."""

    def __init__(self, icons_dir: Path) -> None:
        """Point the store at its directory.

        Args:
            icons_dir: `Settings.poster_icons_dir`. Created on first save;
                not created here, so constructing the store is side-effect free.
        """
        self._icons_dir = icons_dir

    @property
    def directory(self) -> Path:
        """The directory icon filenames resolve against."""
        return self._icons_dir

    def save(self, data: bytes, *, name_norm: str) -> str:
        """Store *data* as a poster icon, returning its bare filename.

        Re-encodes to PNG rather than trusting the upload: the renderer
        needs an alpha channel it can crop to, and a JPEG screenshot has
        none. Downscales anything oversized.

        Args:
            data: Raw uploaded bytes.
            name_norm: The item's normalized name, used to make the
                filename readable. Never used alone — see the module
                docstring on why the content hash is part of it.

        Raises:
            IconRejectedError: Too large, or not a decodable image.
        """
        if len(data) > MAX_UPLOAD_BYTES:
            raise IconRejectedError(
                f"файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ — пришлите картинку поменьше"
            )
        try:
            opened = Image.open(io.BytesIO(data))
            opened.load()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise IconRejectedError(
                "не удалось прочитать картинку — нужен PNG, JPEG или WebP"
            ) from exc

        image: Image.Image = opened.convert("RGBA")
        if max(image.size) > MAX_STORED_SIZE:
            image.thumbnail((MAX_STORED_SIZE, MAX_STORED_SIZE), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = buffer.getvalue()

        digest = hashlib.sha256(encoded).hexdigest()[:_HASH_LENGTH]
        safe_name = _UNSAFE_FILENAME_CHARS.sub("_", name_norm).strip() or "icon"
        filename = f"{safe_name}-{digest}.png"

        self._icons_dir.mkdir(parents=True, exist_ok=True)
        (self._icons_dir / filename).write_bytes(encoded)
        return filename

    def exists(self, filename: str) -> bool:
        """Whether an icon file is actually on disk.

        Args:
            filename: Bare filename as stored on the slot.
        """
        return (self._icons_dir / filename).is_file()

    def delete_if_unused(self, filename: str, *, still_used: set[str]) -> bool:
        """Remove an icon file, unless some other slot still points at it.

        Args:
            filename: Bare filename to remove.
            still_used: Every filename any slot references right now.

        Returns:
            Whether the file was actually removed.
        """
        if filename in still_used:
            return False
        path = self._icons_dir / filename
        if not path.is_file():
            return False
        path.unlink()
        return True
