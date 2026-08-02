"""Generic ordered-tier ladder shared by `ranks.py` and `referrals.py`.

Both ladders behave identically — an ordered sequence of tiers, each
unlocked at a numeric threshold — so the `current`/`next`/`progress`/
`by_label` logic lives here once instead of twice.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


class Tier(Protocol):
    """The minimal shape a ladder tier must have.

    Declared as read-only properties rather than plain attributes: a plain
    `key: str` would require `key` to be both readable *and* settable to
    satisfy the protocol, which a frozen dataclass (by design) never is.
    """

    @property
    def key(self) -> str:
        """Stable identifier, e.g. `"elite"`."""
        ...

    @property
    def label(self) -> str:
        """Exact sheet label, e.g. `"💎 Elite"`."""
        ...

    @property
    def role_id(self) -> int:
        """Discord role id this tier grants."""
        ...

    @property
    def perks(self) -> tuple[str, ...]:
        """Ready-to-display perk lines."""
        ...


@dataclass(frozen=True, slots=True)
class Progress:
    """Progress towards the next tier, for a progress-bar UI."""

    done: int
    need: int
    pct: int


class Ladder[TierT: Tier]:
    """An ordered ladder of tiers, unlocked at increasing numeric thresholds."""

    def __init__(self, tiers: Sequence[TierT], *, threshold_of: Callable[[TierT], int]) -> None:
        """Build a ladder from its tiers.

        Args:
            tiers: The tiers, in any order (sorted internally by threshold).
            threshold_of: Extracts a tier's unlock threshold (e.g. XP,
                referral count).
        """
        self._threshold_of = threshold_of
        self._tiers = tuple(sorted(tiers, key=threshold_of))

    @property
    def tiers(self) -> tuple[TierT, ...]:
        """All tiers, ascending by threshold."""
        return self._tiers

    def current(self, value: int) -> TierT | None:
        """Return the highest tier unlocked at `value`, or `None` if below the first.

        Args:
            value: The player's current XP / referral count / etc.
        """
        reached = [t for t in self._tiers if self._threshold_of(t) <= value]
        return reached[-1] if reached else None

    def next(self, value: int) -> TierT | None:
        """Return the next tier still ahead of `value`, or `None` if maxed out.

        Args:
            value: The player's current XP / referral count / etc.
        """
        upcoming = [t for t in self._tiers if self._threshold_of(t) > value]
        return upcoming[0] if upcoming else None

    def progress(self, value: int) -> Progress:
        """Return progress towards the next tier.

        A maxed-out ladder (already past the last tier) reports 100%.

        Args:
            value: The player's current XP / referral count / etc.
        """
        upcoming = self.next(value)
        if upcoming is None:
            return Progress(done=1, need=1, pct=100)
        reached = self.current(value)
        base = self._threshold_of(reached) if reached is not None else 0
        need = self._threshold_of(upcoming) - base
        done = value - base
        pct = min(100, round(done / need * 100)) if need > 0 else 100
        return Progress(done=done, need=need, pct=pct)

    def perks_of(self, tier: TierT) -> tuple[str, ...]:
        """Return the ready-to-display perk lines for a tier.

        Args:
            tier: A tier from this ladder.
        """
        return tier.perks

    def by_label(self, label: str) -> TierT | None:
        """Look up a tier by its exact sheet label (e.g. `"💎 Elite"`).

        The sheet's `R`/`S` columns store the label text, not a key — this
        is how `ProgressionService` maps a profile's raw rank/referral_role
        string back to a tier for its `role_id` (decision A2: the label
        itself is the source of truth, not a code-computed threshold).

        Args:
            label: The exact label text.
        """
        for tier in self._tiers:
            if tier.label == label:
                return tier
        return None

    def by_role_id(self, role_id: int) -> TierT | None:
        """Look up a tier by its Discord role id.

        Args:
            role_id: A Discord role id.
        """
        for tier in self._tiers:
            if tier.role_id == role_id:
                return tier
        return None

    @property
    def role_ids(self) -> frozenset[int]:
        """Every role id this ladder can grant — the ladder's mutual-exclusion universe."""
        return frozenset(tier.role_id for tier in self._tiers)
