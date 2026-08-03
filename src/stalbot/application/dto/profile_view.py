"""DTOs returned by `ProfileService` (PLAN.md §10.2, §10.3)."""

from dataclasses import dataclass

from stalbot.domain.entities.user_profile import UserProfile


@dataclass(frozen=True, slots=True)
class ProfileView:
    """A profile plus the original-case nick it should be displayed under.

    `UserProfile` itself carries no display-cased nick (M2: only the cache's
    `users.nick_display` column does), so `/profile`/`/referrals` need this
    alongside the raw entity rather than the entity alone.
    """

    profile: UserProfile
    nick_display: str


@dataclass(frozen=True, slots=True)
class ReferredPlayer:
    """One player a referrer brought in, as shown by `/referrals` (PLAN.md §10.3)."""

    nick_display: str
    discord_id: int | None
