"""One-off: seed the owner's confirmed Coins-shop assortment (заявка 13.09.2026 п.2).

The 11 items and 4 categories below are copied from the owner's own
message specifying the shop (13.09.2026), minus one deliberate deviation:
«Личный Сейф» (`reserve_extend` — extending a reservation) is dropped
entirely per the owner's follow-up decision on 15.09.2026 — there is no
reservation system in the bot to extend, and they chose to drop the
privilege rather than build one. Everything else is unchanged; this exists
so the rest doesn't have to be typed into Discord by hand through a dozen
`/shop_item add` calls after each fresh deploy.

Idempotent per item: re-running skips any item whose name already exists
(`ShopService.add_item` raises `DuplicateShopItemError`) rather than
refusing outright or wiping anything — a shop item is never hard-deleted,
so there is nothing to "replace" here the way `import_poster_layouts.py`
replaces a fully-overwritable table.

Two things the owner's text describes but this bot cannot yet do on its
own (kept as the closest automatable equivalent, noted below and in the
item description so nobody mistakes it for the full original text):

- «Кейс "Слепая Удача"» ("...Coins или ценные рандомные бусты") only
  grants Coins — there is no "pick a random boost from the catalog and
  hand it over" mechanism.
- «Торговая гильдия» ("ты и один твой выбранный друг") only boosts the
  buyer — nothing in `ShopService.buy()` today lets a purchase name a
  second recipient.

Automation status of the three effect kinds that needed a decision
(15.09.2026):

- `queue_skip` — fully wired. `TicketsCog._apply_queue_skip` spends the
  effect and renames the ticket channel the moment its author is known.
- `here_ping` — deliberately left manual, per the owner: they grant this
  themselves. `EffectKind.HERE_PING` is intentionally outside
  `AUTOMATIC_KINDS`, so the item card says so honestly.
- `promo_code` — the redeemed-newbie +1 Coin welcome grant is automatic
  (`ShopService.grant_referral_welcome_bonus`, fired from
  `TransactionService` the moment a referrer is first bound); the
  franchise owner's own ongoing reward is not a new mechanic layered on
  top — it is the referral-turnover math `domain.progression.calculator`
  already computes for any referrer, automatically, on every recompute.

Lives in `scripts/`, not `src/` — one-shot seed code, not part of the
coverage denominator (sqlite_migration.md §XI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from stalbot.application.services.shop import ShopService
from stalbot.domain.clock import SystemClock
from stalbot.domain.entities.shop import ShopCategory
from stalbot.domain.errors import DuplicateShopItemError
from stalbot.domain.shop.effects import EffectKind
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.coin_ledger import CoinLedgerRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.infrastructure.cache.repositories.shop import ShopRepository
from stalbot.infrastructure.cache.repositories.xp_ledger import XpLedgerRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SeedItem:
    category_key: str
    name: str
    description: str
    price_coins: int
    effect_kind: str
    effect_value: str | None = None
    duration_days: int | None = None
    uses: int | None = None
    emoji: str | None = None


_CATEGORIES: tuple[ShopCategory, ...] = (
    ShopCategory(
        key="small",
        name="🟢 Мелкие товары",
        description="Отличный выбор для быстрой выгоды или небольшого разгона по опыту.",
        sort_order=0,
    ),
    ShopCategory(
        key="medium",
        name="🟡 Средний класс",
        description="Инструменты для стабильного заработка и работы на средних дистанциях.",
        sort_order=1,
    ),
    ShopCategory(
        key="large",
        name="🔴 Крупный бизнес",
        description="Предложения для крупных дельцов, готовых забирать с рынка максимум.",  # noqa: RUF001
        sort_order=2,
    ),
    ShopCategory(
        key="elite",
        name="👑 Элитные привилегии",
        description=("Уникальный пассивный доход и вечные статусы для настоящих хозяев Клондайка."),
        sort_order=3,
    ),
)

_ITEMS: tuple[_SeedItem, ...] = (
    _SeedItem(
        category_key="small",
        name="Купон «Разовый Сейл»",
        description=(
            "Даёт скидку 1% на покупку бустов или наценку 0.5% на сдачу ресурсов. "
            "Тратится автоматически при следующей сделке."
        ),
        price_coins=15,
        effect_kind=EffectKind.BOTH_PERCENT.value,
        effect_value=json.dumps({"discount": "1", "markup": "0.5"}),
        uses=1,
        emoji="🎟️",
    ),
    _SeedItem(
        category_key="small",
        name="Экспресс-талон",
        description="Позволяет один раз пройти на сделку абсолютно без очереди.",
        price_coins=20,
        effect_kind=EffectKind.QUEUE_SKIP.value,
        uses=1,
        emoji="📌",
    ),
    _SeedItem(
        category_key="small",
        name="Сухой паёк «Трудяга»",
        description="Моментально начисляет +75 XP на баланс.",
        price_coins=35,
        effect_kind=EffectKind.XP_GRANT.value,
        effect_value="75",
        emoji="🍏",
    ),
    _SeedItem(
        category_key="medium",
        name="Коммерческая лицензия",
        description="Целую неделю даёт +1% к наценке при сдаче любых ресурсов.",
        price_coins=50,
        effect_kind=EffectKind.MARKUP_PERCENT.value,
        effect_value="1",
        duration_days=7,
        emoji="💳",
    ),
    _SeedItem(
        category_key="medium",
        name="Карта Закупщика",
        description=(
            "Даёт 2% скидки на покупку бустов на 7 дней. Суммируется с бонусами ролей."  # noqa: RUF001
        ),
        price_coins=65,
        effect_kind=EffectKind.DISCOUNT_PERCENT.value,
        effect_value="2",
        duration_days=7,
        emoji="🔥",
    ),
    _SeedItem(
        category_key="medium",
        name="Контракт с Шёпотом",  # noqa: RUF001
        description=(
            "Позволяет максимально выгодно сдать ресурсы с наценкой 2% на следующие 3 сделки."  # noqa: RUF001
        ),
        price_coins=75,
        effect_kind=EffectKind.MARKUP_PERCENT.value,
        effect_value="2",
        uses=3,
        emoji="⚒️",
    ),
    _SeedItem(
        category_key="medium",
        name="Кейс «Слепая Удача»",
        description=(
            "Гарантированно возвращает случайное количество Coins (от 50 до 150) на баланс."
        ),
        price_coins=90,
        effect_kind=EffectKind.COINS_GRANT.value,
        effect_value="50-150",
        emoji="📦",
    ),
    _SeedItem(
        category_key="large",
        name="Временный контракт «Медиа-Партнёр»",
        description=(
            "Даёт право раз в 3 дня тегать роль @here в канале «📢・информация» "
            "со своими торговыми предложениями."  # noqa: RUF001
        ),
        price_coins=120,
        effect_kind=EffectKind.HERE_PING.value,
        effect_value="3",
        duration_days=14,
        emoji="🤝",
    ),
    _SeedItem(
        category_key="large",
        name="Золотой Абонемент",
        description=(
            "На полмесяца даёт +3% к скидке на покупку и +1.5% к наценке на сдачу хабара."  # noqa: RUF001
        ),
        price_coins=150,
        effect_kind=EffectKind.BOTH_PERCENT.value,
        effect_value=json.dumps({"discount": "3", "markup": "1.5"}),
        duration_days=14,
        emoji="💎",
    ),
    _SeedItem(
        category_key="large",
        name="Торговая гильдия",
        description="На 7 дней даёт +50% к получаемому XP со всех сделок.",  # noqa: RUF001
        price_coins=175,
        effect_kind=EffectKind.XP_MULTIPLIER.value,
        effect_value="50",
        duration_days=7,
        emoji="🤝",
    ),
    _SeedItem(
        category_key="elite",
        name="Личная Франшиза",
        description=(
            "Создание собственного промокода на месяц: новичок, введший код при сделке, "
            "получает 1 Coins, а вам пассивно капает 0.05 Coins со всех его последующих сделок."  # noqa: RUF001
        ),
        price_coins=200,
        effect_kind=EffectKind.PROMO_CODE.value,
        duration_days=30,
        emoji="🏷️",
    ),
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one run did."""

    categories: int
    items_added: int
    items_skipped: tuple[str, ...]
    """Names already present — left untouched."""


async def run(cache_db: CacheDb) -> SeedReport:
    """Upsert the 4 categories and add whichever of the 11 items are missing.

    Args:
        cache_db: An already-connected-or-not `CacheDb` for the live cache.
    """
    connection = await cache_db.connect()
    shop = ShopService(
        ShopRepository(connection),
        PlayersRepository(connection),
        ProgressionRepository(connection),
        CoinLedgerRepository(connection),
        XpLedgerRepository(connection),
        clock=SystemClock(),
    )

    for category in _CATEGORIES:
        await shop.upsert_category(
            category.key,
            category.name,
            description=category.description,
            sort_order=category.sort_order,
        )

    added = 0
    skipped: list[str] = []
    for seed in _ITEMS:
        try:
            await shop.add_item(
                seed.category_key,
                seed.name,
                seed.description,
                seed.price_coins,
                seed.effect_kind,
                effect_value=seed.effect_value,
                duration_days=seed.duration_days,
                uses=seed.uses,
                emoji=seed.emoji,
            )
        except DuplicateShopItemError:
            skipped.append(seed.name)
            continue
        added += 1

    return SeedReport(categories=len(_CATEGORIES), items_added=added, items_skipped=tuple(skipped))


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    report = await run(cache_db)
    await cache_db.close()

    logger.info(
        "Категорий обновлено: %d. Товаров добавлено: %d.", report.categories, report.items_added
    )
    if report.items_skipped:
        logger.info("Уже были в магазине, пропущено: %s", ", ".join(report.items_skipped))


if __name__ == "__main__":
    asyncio.run(main())
