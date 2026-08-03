"""Tests for the `admin_only()` decorator wiring (PLAN.md §5.5)."""

import discord

from stalbot.presentation.checks import _is_administrator, admin_only


async def _dummy(interaction: discord.Interaction) -> None:
    pass


def test_admin_only_attaches_default_permissions_and_runtime_check() -> None:
    decorated = admin_only()(_dummy)

    permissions = decorated.__discord_app_commands_default_permissions__  # type: ignore[attr-defined]
    assert permissions.administrator is True

    checks = decorated.__discord_app_commands_checks__  # type: ignore[attr-defined]
    assert _is_administrator in checks
