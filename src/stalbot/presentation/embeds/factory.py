"""The only place `discord.Embed` objects get built (PLAN.md §5.3).

No cog is allowed to construct `discord.Embed(...)` directly — every embed in
the project goes through `EmbedFactory`, so color, footer, author and
Discord's size limits are enforced in exactly one place.
"""

from datetime import datetime
from typing import Protocol

import discord

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.domain.clock import SystemClock
from stalbot.domain.enums import TicketKind
from stalbot.presentation.embeds.palette import (
    PLATFORM_NAME,
    TICKET_TITLES,
    Color,
    Emoji,
    build_footer,
)


class _Clock(Protocol):
    """Structural stand-in for `application.ports.Clock` (added in M2)."""

    def now(self) -> datetime:
        """Return the current moment, tz-aware in `GMT3`."""
        ...


_TITLE_MAX = 256
_DESCRIPTION_MAX = 4096
_FIELD_NAME_MAX = 256
_FIELD_VALUE_MAX = 1024
_FIELDS_MAX = 25
_TOTAL_MAX = 6000
_TRIM_STEP = 200
_PLACEHOLDER = "—"


def _truncate(text: str, limit: int) -> str:
    """Cut *text* to *limit* characters, marking the cut with an ellipsis."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


class EmbedFactory:
    """Builds every `discord.Embed` the bot sends, enforcing one visual style."""

    def __init__(
        self,
        *,
        clock: _Clock | None = None,
        platform_name: str = PLATFORM_NAME,
        guild_icon_url: str | None = None,
    ) -> None:
        """Create a factory.

        Args:
            clock: Time source used for the footer timestamp. Defaults to a
                real `SystemClock`.
            platform_name: Text shown in the author line.
            guild_icon_url: Icon shown next to *platform_name*; set once the
                bot has resolved the guild (may be `None` before that).
        """
        self._clock = clock or SystemClock()
        self._platform_name = platform_name
        self.guild_icon_url = guild_icon_url

    def success(self, title: str, description: str | None = None) -> discord.Embed:
        """Build a success embed (`#43B581`)."""
        return self._build(Color.SUCCESS, title, description)

    def info(self, title: str, description: str | None = None) -> discord.Embed:
        """Build an informational embed (`#5865F2`)."""
        return self._build(Color.INFO, title, description)

    def warning(self, title: str, description: str | None = None) -> discord.Embed:
        """Build a warning embed (`#FAA61A`)."""
        return self._build(Color.WARNING, title, description)

    def error(self, title: str, description: str | None = None) -> discord.Embed:
        """Build an error embed (`#ED4245`)."""
        return self._build(Color.ERROR, title, description)

    def ticket(
        self, kind: TicketKind, description: str | None = None, *, title: str | None = None
    ) -> discord.Embed:
        """Build a ticket-colored embed (`#2B2D31`).

        Args:
            kind: Selects the default title (`TICKET_TITLES[kind]`).
            description: Embed body.
            title: Overrides the default title — the boost-order editor
                (PLAN.md §11.6) uses `"🧾 Редактор заказа"` instead of the
                panel's `"🎫 Заявка на заказ бустов"`, but still wants the
                same ticket color.
        """
        return self._build(Color.TICKET, title or TICKET_TITLES[kind], description)

    def audit(self, event: AuditEvent) -> discord.Embed:
        """Build the one audit-log embed format (`#9B59B6`, PLAN.md §5.4)."""
        embed = self._build(
            Color.AUDIT, f"{Emoji.AUDIT} Использование команды", None, now=event.occurred_at
        )
        embed.add_field(
            name=f"{Emoji.USER} Пользователь",
            value=_truncate(f"{event.user_display} (ID: {event.user_id})", _FIELD_VALUE_MAX),
            inline=True,
        )
        embed.add_field(
            name=f"{Emoji.CHANNEL} Канал",
            value=_truncate(event.channel_display, _FIELD_VALUE_MAX),
            inline=True,
        )
        embed.add_field(
            name=f"{Emoji.COMMAND} Команда",
            value=_truncate(event.command, _FIELD_VALUE_MAX),
            inline=True,
        )
        embed.add_field(
            name=f"{Emoji.ARGUMENTS} Аргументы",
            value=_truncate(event.arguments or "—", _FIELD_VALUE_MAX),
            inline=False,
        )
        embed.add_field(
            name=f"{Emoji.SUCCESS} Результат",
            value=_truncate(event.result, _FIELD_VALUE_MAX),
            inline=True,
        )
        embed.add_field(
            name=f"{Emoji.DURATION} Длительность",
            value=f"{event.duration_seconds:.2f} с",
            inline=True,
        )
        embed.add_field(name=f"{Emoji.TRACE} Trace", value=event.trace_id, inline=True)
        return enforce_limits(embed)

    def _build(
        self,
        color: int,
        title: str,
        description: str | None,
        *,
        now: datetime | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=_truncate(title, _TITLE_MAX),
            description=_truncate(description, _DESCRIPTION_MAX) if description else None,
            color=color,
        )
        embed.set_author(name=self._platform_name, icon_url=self.guild_icon_url)
        embed.set_footer(text=build_footer(now or self._clock.now()))
        return enforce_limits(embed)


def enforce_limits(embed: discord.Embed) -> discord.Embed:
    """Clamp *embed* to Discord's field-count and per-field/total-length limits.

    `success`/`info`/`warning`/`error`/`ticket` return an embed with no
    fields yet; callers that add fields with `embed.add_field(...)`
    afterwards (paginated lists, reports) must call this again before
    sending, since the factory can only enforce limits at construction time.
    """
    while len(embed.fields) > _FIELDS_MAX:
        embed.remove_field(_FIELDS_MAX)

    for index, field in enumerate(embed.fields):
        name = _truncate(field.name or "​", _FIELD_NAME_MAX)
        value = _truncate(field.value or "​", _FIELD_VALUE_MAX)
        if name != field.name or value != field.value:
            embed.set_field_at(index, name=name, value=value, inline=field.inline)

    while len(embed) > _TOTAL_MAX and embed.fields:
        index = max(range(len(embed.fields)), key=lambda i: len(embed.fields[i].value or ""))
        field = embed.fields[index]
        value = field.value or ""
        if value == _PLACEHOLDER:
            # Every field's value has already been collapsed to the
            # placeholder — shrinking further is impossible (field names
            # are not trimmed here), so stop instead of looping forever.
            break
        trimmed = _truncate(value, max(0, len(value) - _TRIM_STEP)) or _PLACEHOLDER
        embed.set_field_at(index, name=field.name or "​", value=trimmed, inline=field.inline)

    return embed
