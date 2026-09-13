"""Cost of goods and net profit for one boost order (заявка 13.09.2026 п.7).

Joins three things that already exist: the order's draft lines
(`boost_order_lines`), the catalog's bridge into the shelter model
(`catalog_items.shelter_item_id`), and the live cost calculation
(`ShelterCostService.current_costs`). Writes nothing.

Accuracy is bounded by that bridge, and the result says so instead of
hiding it: a line with no `shelter_item_id` lands in `unpriced` rather than
being counted as free. The alternative would make profit look better the
worse the bridge is filled in — exactly backwards from what the owner needs
to see.
"""

from dataclasses import dataclass
from decimal import Decimal

from stalbot.application.services.boost_orders import BoostOrderService
from stalbot.application.services.shelter_cost import ShelterCostService

_KOPEKS_IN_RUB = Decimal(100)


@dataclass(frozen=True, slots=True)
class OrderLineCost:
    """One order line together with what it costs us to supply."""

    name: str
    quantity: int
    unit_cost_kopeks: int | None
    """`None` when the item has no shelter bridge, or its cost is unresolved."""

    @property
    def total_cost(self) -> Decimal:
        """The whole line's cost in rubles (`0` for an unpriced line)."""
        if self.unit_cost_kopeks is None:
            return Decimal(0)
        return Decimal(self.unit_cost_kopeks * self.quantity) / _KOPEKS_IN_RUB


@dataclass(frozen=True, slots=True)
class OrderEconomics:
    """One order's revenue, cost of goods, and net profit."""

    revenue: Decimal
    """What the player actually paid — after the rank markup and any coupon."""
    priced: tuple[OrderLineCost, ...]
    unpriced: tuple[OrderLineCost, ...]
    """Lines whose cost is unknown. They are excluded from `cost`, which is
    why `profit` is an upper bound whenever this is non-empty."""

    @property
    def cost(self) -> Decimal:
        """Total cost of the priced lines, in rubles."""
        return sum((line.total_cost for line in self.priced), Decimal(0))

    @property
    def profit(self) -> Decimal:
        """Revenue minus cost. Overstated while any line is unpriced."""
        return self.revenue - self.cost

    @property
    def margin_percent(self) -> Decimal | None:
        """Profit as a share of revenue, or `None` when revenue is zero."""
        if self.revenue == 0:
            return None
        return self.profit / self.revenue * 100

    @property
    def is_complete(self) -> bool:
        """Whether every line could be priced — if not, treat profit as a ceiling."""
        return not self.unpriced


class OrderEconomicsService:
    """Computes cost of goods and profit from a boost order's draft lines."""

    def __init__(self, boost_orders: BoostOrderService, shelter_cost: ShelterCostService) -> None:
        """Wire the service to its collaborators.

        Args:
            boost_orders: Source of the order's lines and their catalog rows.
            shelter_cost: Live cost-of-goods across the whole shelter model.
        """
        self._boost_orders = boost_orders
        self._shelter_cost = shelter_cost

    async def for_order(self, channel_id: int, revenue: Decimal) -> OrderEconomics:
        """Compute the economics of the order drafted in *channel_id*.

        Must be called before `BoostOrderService.clear()` — once the order
        is confirmed, its lines are gone.

        Args:
            channel_id: The boost-order ticket's channel.
            revenue: The amount actually recorded on the deal, in rubles.
        """
        lines = await self._boost_orders.list_lines_with_items(channel_id)
        costs = await self._shelter_cost.current_costs() if lines else {}

        priced: list[OrderLineCost] = []
        unpriced: list[OrderLineCost] = []
        for line, item in lines:
            if item is None:
                continue  # deleted from the catalog between drafting and confirming
            result = costs.get(item.shelter_item_id) if item.shelter_item_id is not None else None
            unit_cost = result.cost_kopeks if result is not None else None
            entry = OrderLineCost(
                name=item.name, quantity=line.quantity, unit_cost_kopeks=unit_cost
            )
            (priced if unit_cost is not None else unpriced).append(entry)

        return OrderEconomics(revenue=revenue, priced=tuple(priced), unpriced=tuple(unpriced))
