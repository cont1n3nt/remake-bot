"""Saves a copy of every ticket screenshot to disk for a future OCR dataset (PLAN.md §11.8).

Gated by `Settings.ocr_keep_samples` (on by default, decision A7) — this is
the single most important groundwork item for M13: OCR on Stalcraft
screenshots (custom font, colored background, Discord's own recompression)
has to be tuned empirically, and that needs a few hundred real examples.
Collecting them from day one of M9 means a usable dataset already exists by
the time M13 starts.
"""

import asyncio
import re
from pathlib import Path

#: Bare alphanumeric extension, 1-8 characters — long enough for any real
#: image format (`png`, `jpeg`, `webp`, ...), short enough to reject
#: anything trying to smuggle a path separator or `..` through this
#: parameter (INFRA2-4). Not reachable via today's one caller (which always
#: passes a constant or a filename-derived extension that already looks
#: like this), but validated at this function's own boundary rather than
#: trusted from whichever caller happens to exist today.
_EXTENSION_RE = re.compile(r"[A-Za-z0-9]{1,8}")


async def save_sample(samples_dir: Path, sha256: str, data: bytes, *, extension: str) -> Path:
    """Write *data* to `<samples_dir>/<sha256>.<extension>`, creating the directory if needed.

    Args:
        samples_dir: Root directory for collected samples (`Settings.ocr_samples_dir`).
        sha256: Hex digest of *data*, used as the filename stem — an
            identical screenshot re-sent to another ticket collapses to the
            same file instead of piling up duplicates.
        data: Original, unmodified screenshot bytes (PLAN.md §11.8: no
            recompression, so the sample matches exactly what Discord served).
        extension: File extension without a leading dot, e.g. `"png"`.

    Returns:
        The path written to.

    Raises:
        ValueError: If `extension` is not a bare alphanumeric token —
            guards against a path separator or `..` reaching the filename.
    """
    if not _EXTENSION_RE.fullmatch(extension):
        raise ValueError(f"unsafe sample file extension: {extension!r}")
    samples_dir.mkdir(parents=True, exist_ok=True)
    path = samples_dir / f"{sha256}.{extension}"
    await asyncio.to_thread(path.write_bytes, data)
    return path
