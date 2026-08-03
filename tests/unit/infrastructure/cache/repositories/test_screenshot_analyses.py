"""Tests for `ScreenshotAnalysesRepository` against a real (temp-file) SQLite connection."""

import aiosqlite

from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)


async def test_record_inserts_a_row(connection: aiosqlite.Connection) -> None:
    repo = ScreenshotAnalysesRepository(connection)

    await repo.record(
        channel_id=111,
        sha256="abc123",
        image_url="https://cdn.discordapp.com/attachments/1/2/screenshot.png",
        sample_path="/data/ocr_samples/abc123.png",
        size_bytes=2048,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:00:00+03:00",
    )

    cursor = await connection.execute(
        "SELECT * FROM screenshot_analyses WHERE image_sha256 = ?", ("abc123",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["channel_id"] == 111
    assert row["status"] == "disabled"
    assert row["size_bytes"] == 2048


async def test_record_with_the_same_sha_updates_the_existing_row(
    connection: aiosqlite.Connection,
) -> None:
    repo = ScreenshotAnalysesRepository(connection)
    await repo.record(
        channel_id=111,
        sha256="dup",
        image_url=None,
        sample_path=None,
        size_bytes=100,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:00:00+03:00",
    )

    await repo.record(
        channel_id=222,
        sha256="dup",
        image_url="https://cdn.discordapp.com/attachments/1/2/screenshot.png",
        sample_path="/data/ocr_samples/dup.png",
        size_bytes=200,
        mime="image/jpeg",
        status="done",
        created_at="2026-08-02T12:05:00+03:00",
    )

    cursor = await connection.execute(
        "SELECT * FROM screenshot_analyses WHERE image_sha256 = ?", ("dup",)
    )
    rows = list(await cursor.fetchall())
    assert len(rows) == 1
    assert rows[0]["channel_id"] == 222
    assert rows[0]["size_bytes"] == 200
    assert rows[0]["status"] == "done"


async def test_record_confirmed_amount_labels_every_screenshot_of_the_channel(
    connection: aiosqlite.Connection,
) -> None:
    repo = ScreenshotAnalysesRepository(connection)
    await repo.record(
        channel_id=111,
        sha256="a",
        image_url=None,
        sample_path=None,
        size_bytes=1,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:00:00+03:00",
    )
    await repo.record(
        channel_id=111,
        sha256="b",
        image_url=None,
        sample_path=None,
        size_bytes=1,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:01:00+03:00",
    )

    await repo.record_confirmed_amount(111, "299900")

    cursor = await connection.execute(
        "SELECT total_estimate FROM screenshot_analyses WHERE channel_id = ? ORDER BY image_sha256",
        (111,),
    )
    rows = list(await cursor.fetchall())
    assert [row["total_estimate"] for row in rows] == ["299900", "299900"]


async def test_record_confirmed_amount_is_a_no_op_for_an_unknown_channel(
    connection: aiosqlite.Connection,
) -> None:
    repo = ScreenshotAnalysesRepository(connection)

    await repo.record_confirmed_amount(999, "299900")  # must not raise


async def test_count_all_counts_every_distinct_screenshot(connection: aiosqlite.Connection) -> None:
    repo = ScreenshotAnalysesRepository(connection)
    assert await repo.count_all() == 0

    await repo.record(
        channel_id=111,
        sha256="a",
        image_url=None,
        sample_path=None,
        size_bytes=1,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:00:00+03:00",
    )
    await repo.record(
        channel_id=222,
        sha256="b",
        image_url=None,
        sample_path=None,
        size_bytes=1,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:01:00+03:00",
    )

    assert await repo.count_all() == 2


async def test_count_with_confirmed_amount_only_counts_labeled_rows(
    connection: aiosqlite.Connection,
) -> None:
    repo = ScreenshotAnalysesRepository(connection)
    await repo.record(
        channel_id=111,
        sha256="a",
        image_url=None,
        sample_path=None,
        size_bytes=1,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:00:00+03:00",
    )
    await repo.record(
        channel_id=222,
        sha256="b",
        image_url=None,
        sample_path=None,
        size_bytes=1,
        mime="image/png",
        status="disabled",
        created_at="2026-08-02T12:01:00+03:00",
    )

    assert await repo.count_with_confirmed_amount() == 0

    await repo.record_confirmed_amount(111, "299900")

    assert await repo.count_with_confirmed_amount() == 1
