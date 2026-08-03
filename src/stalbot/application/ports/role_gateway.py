"""Port for syncing a member's progression roles (PLAN.md §3.1)."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RoleSet:
    """What a member's ladder roles should look like after a sync.

    `universe` is every role id the sync is allowed to touch (e.g. all five
    rank role ids); `desired` is the subset of it the member should end up
    holding. Restricting removal to `universe` is what makes ladders
    mutually exclusive without the gateway needing to know anything about
    ranks or referrals — granting a new rank role and *not* including the
    old one in `desired` is enough for it to be revoked.
    """

    desired: frozenset[int]
    universe: frozenset[int]


@dataclass(frozen=True, slots=True)
class RoleDiff:
    """What a `sync_roles` call actually changed."""

    granted: tuple[int, ...]
    revoked: tuple[int, ...]


class RoleGateway(Protocol):
    """Reconciles a Discord member's roles against a desired `RoleSet`."""

    async def sync_roles(self, member_id: int, target: RoleSet) -> RoleDiff:
        """Grant/revoke roles so the member ends up holding exactly `target.desired`.

        Args:
            member_id: Discord member id.
            target: Desired roles and the universe `sync_roles` may revoke from.

        Returns:
            The roles actually granted/revoked (empty on both sides if the
            member already matched, or could not be resolved).
        """
        ...
