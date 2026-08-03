"""SQLite-backed `screenshot_analyses` (PLAN.md §8.1, §11.8) — OCR groundwork.

Write-only from `ScreenshotService`'s point of view in v1.0: nothing reads
this table back yet (M13 will, to feed the matcher/dataset tooling). Keyed
by `image_sha256` so a screenshot re-sent to a different ticket updates its
one row instead of accumulating duplicates.
"""

import aiosqlite


class ScreenshotAnalysesRepository:
    """Records one bookkeeping row per distinct screenshot seen in a ticket."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def record(
        self,
        *,
        channel_id: int,
        sha256: str,
        image_url: str | None,
        sample_path: str | None,
        size_bytes: int,
        mime: str,
        status: str,
        created_at: str,
    ) -> None:
        """Insert one screenshot's bookkeeping row, or update it if already seen.

        Args:
            channel_id: Ticket channel the screenshot was attached in.
            sha256: Hex digest of the original bytes — the table's natural
                key (`ix_shots_sha`).
            image_url: The log-channel's permanent CDN URL (PLAN.md §11.5).
            sample_path: Where the dataset copy was written, or `None` if
                `OCR_KEEP_SAMPLES` is off.
            size_bytes: Original byte size.
            mime: Content type, e.g. `"image/png"`.
            status: `OcrResult.status` (always `"disabled"` in v1.0).
            created_at: ISO timestamp.
        """
        await self._conn.execute(
            """
            INSERT INTO screenshot_analyses
                (channel_id, image_sha256, image_url, sample_path,
                 size_bytes, mime, status, created_at)
            VALUES
                (:channel_id, :sha256, :image_url, :sample_path,
                 :size_bytes, :mime, :status, :created_at)
            ON CONFLICT (image_sha256) DO UPDATE SET
                channel_id = excluded.channel_id,
                image_url = excluded.image_url,
                sample_path = excluded.sample_path,
                size_bytes = excluded.size_bytes,
                mime = excluded.mime,
                status = excluded.status,
                created_at = excluded.created_at
            """,
            {
                "channel_id": channel_id,
                "sha256": sha256,
                "image_url": image_url,
                "sample_path": sample_path,
                "size_bytes": size_bytes,
                "mime": mime,
                "status": status,
                "created_at": created_at,
            },
        )
        await self._conn.commit()

    async def record_confirmed_amount(self, channel_id: int, amount: str) -> None:
        """Label every screenshot of this ticket with the admin-confirmed deal amount.

        PLAN.md §11.5/§11.8: once a human confirms the real number, that
        becomes ground truth for a future OCR training pair — screenshot
        in, correct total out. Every screenshot attached to the ticket
        gets the same label; v1.0 tickets only ever have one, so this is
        not yet a precision concern.

        Args:
            channel_id: The confirmed ticket's channel.
            amount: The confirmed deal amount, as a `Decimal`-parseable string.
        """
        await self._conn.execute(
            "UPDATE screenshot_analyses SET total_estimate = ? WHERE channel_id = ?",
            (amount, channel_id),
        )
        await self._conn.commit()

    async def count_all(self) -> int:
        """Return the number of distinct screenshots collected so far.

        `/healthcheck`'s OCR-dataset readiness counter (PLAN.md §12, M11):
        M13 needs at least ~150 of these before OCR tuning can start.
        """
        cursor = await self._conn.execute("SELECT COUNT(*) AS n FROM screenshot_analyses")
        row = await cursor.fetchone()
        return int(row["n"]) if row is not None else 0

    async def count_with_confirmed_amount(self) -> int:
        """Return how many collected screenshots also have a ground-truth amount.

        These are the `record_confirmed_amount`-labeled rows — real
        training pairs, not just raw samples (PLAN.md §12, M11: at least
        ~50 needed).
        """
        cursor = await self._conn.execute(
            "SELECT COUNT(*) AS n FROM screenshot_analyses WHERE total_estimate IS NOT NULL"
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row is not None else 0
