"""DTOs for `/new_price`'s parse-validate-diff-apply flow (PLAN.md §10.6)."""

from dataclasses import dataclass, field

from stalbot.application.dto.price_change import PriceChange


@dataclass(frozen=True, slots=True)
class PriceImportIssue:
    """One rejected line from an imported price list TXT."""

    line_number: int
    message: str


@dataclass(frozen=True, slots=True)
class PriceImportPlan:
    """The result of parsing and validating a price list TXT, before applying it.

    PLAN.md §10.6 step 4: every issue in the file is collected up front and
    shown at once; if `issues` is non-empty, nothing is ever applied.
    """

    changes: tuple[PriceChange, ...] = field(default_factory=tuple)
    issues: tuple[PriceImportIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        """Whether the file had zero validation issues."""
        return not self.issues
