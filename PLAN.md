# PLAN.md — Discord-бот для Stalcraft (Stalzone) × Google Sheets

> Документ описывает архитектуру и поэтапный план реализации бота **с нуля**.
> Предыдущий код проекта считается deprecated и в плане не используется.

---

## 0. Оглавление

1. [Цели, границы, критерии качества](#1-цели-границы-критерии-качества)
2. [Технологический стек](#2-технологический-стек)
3. [Архитектура](#3-архитектура)
4. [Структура проекта](#4-структура-проекта)
5. [Сквозные модули (core)](#5-сквозные-модули-core)
6. [Модель данных и карта таблицы](#6-модель-данных-и-карта-таблицы)
7. [Слой Google Sheets](#7-слой-google-sheets)
8. [Слой кэша (SQLite)](#8-слой-кэша-sqlite)
9. [Домен прогрессии: ранги, рефералы, бонусы](#9-домен-прогрессии-ранги-рефералы-бонусы)
10. [Спецификация команд](#10-спецификация-команд)
11. [Система тикетов](#11-система-тикетов)
12. [Логирование, ошибки, наблюдаемость](#12-логирование-ошибки-наблюдаемость)
13. [Тестирование и качество](#13-тестирование-и-качество)
14. [Деплой и эксплуатация](#14-деплой-и-эксплуатация)
15. [Этапы работ (Milestones)](#15-этапы-работ-milestones)
16. [Риски](#16-риски)
17. [Принятые решения и оставшиеся мелочи](#17-принятые-решения-и-оставшиеся-мелочи)

> Задел под OCR скриншотов — §11.8, реализация — этап M13 (§15).

---

## 1. Цели, границы, критерии качества

### 1.1 Цель

Discord-бот, который является единственным интерфейсом к Google-таблице торговой площадки:
фиксация сделок, профили/прогрессия игроков, реферальная система, база предметов и цен,
статистика по периодам и полуавтоматическая обработка тикетов (заявок).

### 1.2 Принятые архитектурные решения (подтверждены заказчиком)

| # | Решение | Выбор |
|---|---------|-------|
| A1 | Доступ к данным | **SQLite-кэш + write-through.** Чтение — из локальной БД, запись — в Sheets с последующей инвалидацией/точечным обновлением кэша. Плюс фоновая полная синхронизация. |
| A2 | Источник истины для Coins / XP / Ранга / Реф-роли | **Формулы в таблице.** Бот пишет только «сырые» данные (Тикеты, Discord ID, флаг бустера) и читает уже вычисленные `K`, `L`, `R`, `S`. Бизнес-логика не дублируется. |
| A3 | Детект повышения ранга / реф-роли | **Гибрид:** проверка затронутых игроков сразу после сделки + фоновый поллер по всей базе раз в N минут (ловит ручные правки таблицы, буст сервера, изменения цен). |
| A4 | Состояние тикетов | **SQLite.** Persistent views: кнопки живут после рестарта, черновик заказа бустов не теряется. |
| A5 | Изменение формул | **Запрещено.** Формулы уже работают; бот их не пишет, не правит и не заменяет на `ARRAYFORMULA` (§7.3). Диапазоны продлевает вручную заказчик. |
| A6 | Все пороги и коэффициенты | **Берутся из формул**, а не из текстового описания системы. Тексты бонусов в embed'ах приведены к формулам (§9.1.1). |
| A7 | OCR скриншотов | **Не входит в v1.0, но место под него готовится сразу** (§11.8): порт `OcrGateway` + `NullOcrGateway`, доменные DTO, колонки в БД и сбор датасета образцов с первого дня. Реализация — этап M13; включение = замена одной строки в `bootstrap.py`. |

### 1.3 Критерии качества кода (Middle+/Senior)

- Полные **type hints** на всех публичных и приватных функциях; `mypy --strict` без ошибок.
- **Docstrings** (Google style) для всех модулей, классов, публичных методов: назначение, аргументы, возврат, исключения.
- **Clean Architecture**: домен не знает ни про Discord, ни про Google Sheets. Зависимости направлены внутрь, наружу — через `Protocol`-порты.
- Никакого «бога-объекта»: cog содержит только связывание UI ↔ use-case, вся логика — в `application/`.
- Никаких «магических чисел» и голых ID в коде — всё в `config/`.
- Явные доменные исключения вместо `return None` / `except Exception: pass`.
- `ruff` (включая `I`, `N`, `D`, `ANN`, `RUF`) без замечаний.
- Покрытие тестами: домен и парсеры — ≥ 95 %, сервисы — ≥ 80 %.

### 1.4 Вне границ первой версии, но с заделом в архитектуре

Функции ниже в v1.0 не реализуются, однако **место под них закладывается сразу**, чтобы
подключение не потребовало переписывания уже работающего кода.

| Функция | Задел в v1.0 | Подробности |
|---------|--------------|-------------|
| **OCR скриншотов** | Порт `OcrGateway` + `NullOcrGateway`, доменные DTO, колонки в БД, сбор датасета образцов с первого дня работы тикетов | §11.8, этап M13 |
| Встроенный калькулятор `/calc` | `evaluate_amount()` пишется сразу как полноценный вычислитель выражений | §5.1 |
| Промокоды Барона, ежемесячный доход Legend | требуют планировщика начислений | бэклог |
| Веб-панель администратора | — | не планируется |

---

## 2. Технологический стек

| Слой | Выбор | Обоснование |
|------|-------|-------------|
| Язык | Python 3.12 | `match`, `Self`, `type` alias, лучший тайпинг |
| Discord | `discord.py >= 2.4` | app_commands, Modals, persistent Views, autocomplete |
| Google Sheets | `gspread` + `google-auth` (Service Account) | batchGet/batchUpdate, контроль над `valueInputOption` |
| Асинхронность к Sheets | `asyncio.to_thread` + собственный rate limiter | Sheets SDK синхронный; блокировать event loop нельзя |
| Кэш | `aiosqlite` + ручные SQL-миграции | Ноль лишних зависимостей, полный контроль над схемой |
| Конфиг | `pydantic-settings` | Валидация `.env` на старте, fail-fast |
| Логи | `structlog` → stdout (JSON) + ротация в файл | Машиночитаемые логи + trace_id |
| Тесты | `pytest`, `pytest-asyncio`, `hypothesis` (для парсера денег) | |
| Качество | `ruff`, `mypy`, `pre-commit` | |
| Планировщик | `discord.ext.tasks` | Не тянем APScheduler ради двух задач |
| OCR *(M13, не в v1.0)* | `opencv-python` + `Pillow` для препроцессинга; движок — `pytesseract` / `paddleocr` / Google Cloud Vision | Выбирается по результатам на реальном датасете (§11.8). Ставится как **опциональная** зависимость `pip install -e .[ocr]`, чтобы v1.0 не тянула тяжёлые пакеты |

---

## 3. Архитектура

Четыре слоя, зависимости строго внутрь:

```
┌───────────────────────────────────────────────────────────────┐
│ presentation/   cogs, slash-команды, Views, Modals, Embeds     │
│                 autocomplete, checks, error handler            │
└───────────────────────────┬───────────────────────────────────┘
                            │ вызывает use-cases
┌───────────────────────────▼───────────────────────────────────┐
│ application/    сервисы (use-cases) + ПОРТЫ (Protocol)         │
│                 TransactionService, ProfileService, ...        │
└───────────────────────────┬───────────────────────────────────┘
                            │ использует
┌───────────────────────────▼───────────────────────────────────┐
│ domain/         сущности, value objects, правила, ошибки       │
│                 money, nick, time, ranks, referrals            │
│                 ЧИСТЫЙ PYTHON, НОЛЬ I/O                        │
└───────────────────────────────────────────────────────────────┘
                            ▲ реализует порты
┌───────────────────────────┴───────────────────────────────────┐
│ infrastructure/ SheetsClient, SQLite-репозитории,              │
│                 DiscordRoleGateway, AuditChannelGateway        │
└───────────────────────────────────────────────────────────────┘
```

### 3.1 Порты (интерфейсы в `application/ports/`)

```python
class TransactionRepository(Protocol):
    async def add(self, tx: NewTransaction) -> TransactionRecord: ...
    async def list_by_period(self, period: DateRange) -> Sequence[TransactionRecord]: ...
    async def list_page(self, page: int, size: int) -> Page[TransactionRecord]: ...

class UserRepository(Protocol):
    async def get_by_nick(self, nick: NormalizedNick) -> UserProfile | None: ...
    async def get_by_discord_id(self, discord_id: int) -> UserProfile | None: ...
    async def bind_discord(self, nick: NormalizedNick, discord_id: int) -> None: ...
    async def list_referrals_of(self, nick: NormalizedNick) -> Sequence[UserProfile]: ...
    async def all(self) -> Sequence[UserProfile]: ...

class ItemRepository(Protocol):
    async def all(self) -> Sequence[Item]: ...
    async def by_category(self, category: ItemCategory) -> Sequence[Item]: ...
    async def find(self, name: str, category: ItemCategory | None) -> Item | None: ...
    async def add(self, draft: NewItem) -> Item: ...
    async def delete(self, item_id: int) -> Item: ...
    async def update_prices(self, changes: Sequence[PriceChange]) -> None: ...

class PriceSheetGateway(Protocol):      # листы Мейн Скуп / Скуп бустов / БУСТЫ
    async def read_layout(self, layout: SheetLayout) -> Sequence[PriceCell]: ...
    async def write_prices(self, cells: Sequence[PriceCell]) -> None: ...

class RoleGateway(Protocol):
    async def sync_roles(self, member_id: int, target: RoleSet) -> RoleDiff: ...

class AuditGateway(Protocol):
    async def emit(self, event: AuditEvent) -> None: ...

class Clock(Protocol):
    def now(self) -> datetime: ...      # всегда tz-aware, GMT+3

class OcrGateway(Protocol):             # ★ задел под M13, в v1.0 — NullOcrGateway
    @property
    def enabled(self) -> bool: ...
    async def recognize(self, image: ScreenshotImage) -> OcrResult: ...
```

### 3.2 Композиционный корень

`bootstrap.py` собирает граф зависимостей вручную (без DI-фреймворка):
`Settings → SheetsClient → CacheDb → репозитории → сервисы → Bot → cogs`.
Каждый cog получает готовые сервисы через конструктор, а не лезет в глобалы.

---

## 4. Структура проекта

```
remake-bot/
├─ pyproject.toml                 # deps, ruff, mypy, pytest
├─ .env.example
├─ README.md
├─ PLAN.md  /  PLAN_PROGRESS.md
├─ credentials/                   # service_account.json (в .gitignore!)
├─ src/stalbot/
│  ├─ __main__.py                 # точка входа
│  ├─ bootstrap.py                # композиционный корень
│  ├─ config/
│  │   ├─ settings.py             # pydantic-settings, читает .env
│  │   ├─ ids.py                  # ID каналов, категорий, ролей, бота Ticket Tool
│  │   └─ constants.py            # пороги, цвета, лимиты Discord
│  ├─ domain/
│  │   ├─ money.py                # ★ ЕДИНЫЙ парсер/форматтер + калькулятор
│  │   ├─ nick.py                 # normalize_nick()
│  │   ├─ clock.py                # GMT+3, DateRange, парс дат
│  │   ├─ enums.py                # DealType, ItemCategory, TicketKind, ...
│  │   ├─ entities/
│  │   │   ├─ transaction.py
│  │   │   ├─ user_profile.py
│  │   │   ├─ item.py
│  │   │   ├─ ticket.py
│  │   │   ├─ boost_order.py
│  │   │   └─ screenshot.py       # ★ ScreenshotImage, OcrResult, RecognizedItem (задел M13)
│  │   ├─ progression/
│  │   │   ├─ ranks.py            # RankLadder
│  │   │   ├─ referrals.py        # ReferralLadder
│  │   │   └─ perks.py            # описания бонусов для UI
│  │   └─ errors.py
│  ├─ application/
│  │   ├─ ports/
│  │   ├─ dto/
│  │   └─ services/
│  │       ├─ transactions.py     # /add и подтверждение тикета
│  │       ├─ profile.py          # /profile
│  │       ├─ referrals.py        # /referrals, /set_referral
│  │       ├─ catalog.py          # item database CRUD
│  │       ├─ pricing.py          # /setprice /setboost /new_price /sync_prices
│  │       ├─ stats.py            # /logs /day /week /month
│  │       ├─ progression.py      # детект повышений + выдача ролей
│  │       ├─ tickets.py          # жизненный цикл заявок
│  │       ├─ screenshots.py      # ★ приём скриншота + вызов OCR (задел M13)
│  │       └─ audit.py            # единый лог использования команд
│  ├─ infrastructure/
│  │   ├─ sheets/
│  │   │   ├─ client.py           # низкоуровневый батч-клиент
│  │   │   ├─ ratelimit.py        # token bucket + backoff
│  │   │   ├─ a1.py               # A1-нотация, колонки ↔ индексы
│  │   │   ├─ layouts.py          # ★ декларативная карта листов/колонок
│  │   │   ├─ protection.py       # ★ READ_ONLY_RANGES: запрет записи в формульные колонки
│  │   │   └─ repositories/       # реализация портов поверх Sheets
│  │   ├─ cache/
│  │   │   ├─ db.py               # соединение, миграции
│  │   │   ├─ schema.sql
│  │   │   ├─ repositories/       # реализация портов поверх SQLite
│  │   │   └─ sync.py             # фоновая синхронизация Sheets → SQLite
│  │   ├─ discord/
│  │   │   ├─ role_gateway.py
│  │   │   ├─ audit_channel.py
│  │   │   └─ emoji_resolver.py   # имя эмодзи → <:name:id>
│  │   ├─ ocr/                    # ★ ЗАДЕЛ ПОД M13
│  │   │   ├─ null.py             # NullOcrGateway — используется в v1.0
│  │   │   ├─ preprocess.py       # upscale, grayscale, CLAHE, бинаризация, кроп
│  │   │   ├─ matcher.py          # распознанный текст → предмет из item database
│  │   │   ├─ tesseract.py        # движок 1 (pytesseract, rus+eng)
│  │   │   ├─ paddle.py           # движок 2 (PaddleOCR)
│  │   │   ├─ vision.py           # движок 3 (Google Cloud Vision)
│  │   │   └─ samples.py          # сбор датасета образцов (работает с M9!)
│  │   └─ logging/setup.py
│  └─ presentation/
│      ├─ bot.py                  # subclass commands.Bot, регистрация cogs/views
│      ├─ cogs/
│      │   ├─ transactions.py     # /add
│      │   ├─ profile.py          # /profile /referrals
│      │   ├─ catalog.py          # /item_add /del_item /price_list /give_price
│      │   ├─ pricing.py          # /new_price /setprice /setboost /sync_prices
│      │   ├─ stats.py            # /logs /day /week /month
│      │   ├─ manual.py           # /set_referral /set_rank
│      │   ├─ tag.py              # /tag
│      │   └─ tickets/            # слушатель категорий + все View/Modal
│      ├─ embeds/
│      │   ├─ factory.py          # ★ единый конструктор embed'ов
│      │   ├─ palette.py          # цвета/иконки/футер
│      │   └─ progress.py         # прогресс-бары
│      ├─ views/                  # переиспользуемые View (пагинация, подтверждение)
│      ├─ modals/
│      ├─ autocomplete.py
│      ├─ checks.py               # @admin_only
│      └─ errors.py               # глобальный обработчик app_command ошибок
└─ tests/
   ├─ unit/                       # домен, парсеры, лестницы рангов
   ├─ integration/                # фейковые Sheets/SQLite
   └─ fixtures/
```

---

## 5. Сквозные модули (core)

### 5.1 ★ `domain/money.py` — единая работа с денежными числами

**Самое важное требование ТЗ.** Один модуль, используемый везде: `/add`, подтверждение тикета,
`/setprice`, `/setboost`, `/new_price`, расчёт заказа бустов, будущий калькулятор.

#### Публичный API

```python
def parse_amount(raw: str) -> Decimal:
    """Разобрать денежное значение в любом человеческом формате.

    Поддерживает: "10 000", "10 000", "299 900 ₽", "299900руб", "1 500 000 р.",
    "1,5кк", "1.5kk", "10к", "250k", "3ккк", "1_000_000", "299,900".

    Raises:
        AmountParseError: если строку невозможно интерпретировать однозначно.
    """

def evaluate_amount(expression: str) -> Decimal:
    """Вычислить арифметическое выражение над денежными значениями.

    Пример: "299 900 ₽ + 10000"  -> Decimal("309900")
            "1.5кк * 3 - 250к"   -> Decimal("4250000")
    Разрешены: + - * / // % ** ( ) и унарный минус. eval() НЕ используется.
    """

def format_amount(value: Decimal | int, *, currency: bool = True) -> str:
    """Отформатировать для вывода: 299900 -> '299 900 ₽' (узкий неразрывный пробел)."""

def format_compact(value: Decimal | int) -> str:
    """Компактно для тесных мест: 1500000 -> '1.5 кк'."""
```

#### Алгоритм `parse_amount` (пошагово)

1. **Нормализация Unicode**: NFKC; замена ` `, ` `, ` ` (неразрывные/узкие пробелы) на обычный пробел.
2. **Срезание валюты**: удалить `₽ P р руб руб. rub $ €` (регистронезависимо, только по краям токена).
3. **Склейка разрядов**: регулярка `(?<=\d)[ _](?=\d{3}(?!\d))` → `""`.
   Именно это позволяет `10 000 + 5 000` работать: пробел-разделитель разрядов исчезает,
   а пробел вокруг `+` остаётся.
4. **Множители-суффиксы**: `к|k` = ×10³, `кк|kk|m|м|кк` = ×10⁶, `ккк|kkk|b|ккк` = ×10⁹.
   Применяются к непосредственно предшествующему числу.
5. **Десятичный разделитель**: `,` → `.` **только если** после запятой не ровно 3 цифры
   либо в строке уже есть точка. Иначе `,` трактуется как разделитель разрядов.
   *(Правило документируется; неоднозначные случаи вроде «1,500» решаются в пользу разрядов.)*
6. Результат → `Decimal`. Числа хранятся как `Decimal`, в таблицу пишутся как `int`
   (все суммы в игре целые).

#### Алгоритм `evaluate_amount`

1. Шаги 1–5 выше применяются **к числовым токенам**, а не ко всей строке
   (лексер выделяет числа регуляркой, операторы оставляет).
2. Полученная «чистая» строка (`309900+10000`) парсится через `ast.parse(mode="eval")`.
3. Обход AST с **белым списком узлов**: `Expression, BinOp, UnaryOp, Constant, Add, Sub, Mult,
   Div, FloorDiv, Mod, Pow, USub, UAdd`. Всё остальное → `AmountParseError`.
4. Защита: ограничение длины строки (256), ограничение показателя степени, глубины AST.
   `eval`/`exec` не применяются никогда.

#### Тесты (обязательный набор)

Таблица параметризованных кейсов ≥ 60 строк + `hypothesis`-свойство
«`parse_amount(format_amount(x)) == x`» для всех `x` в диапазоне 0…10¹².

---

### 5.2 `domain/clock.py` — время только GMT+3

```python
GMT3: Final = timezone(timedelta(hours=3))

class SystemClock:
    def now(self) -> datetime: return datetime.now(GMT3)
    def today(self) -> date:   return self.now().date()
```

- **Ни одного** `datetime.now()` без tz в проекте (проверяется правилом ruff `DTZ`).
- Все даты, приходящие из таблицы, парсятся в `date`/`datetime` с `tzinfo=GMT3`.
- Единый формат вывода: `31.07.2026`, `31.07.2026 21:45`, в embed — `discord.utils.format_dt` не
  используется (он показывает локальное время пользователя, что противоречит требованию GMT+3).
- `DateRange` (value object) с фабриками `.day(d)`, `.week(start, end)`, `.month(year, month)`.
- Граница суток для нумерации сделок в `/logs` — `00:00:00 GMT+3`.

---

### 5.3 `presentation/embeds/factory.py` — единый визуальный стиль

Все embed'ы строятся **только** через фабрику. Никаких `discord.Embed(...)` в cog'ах.

```python
class EmbedFactory:
    def success(self, title: str, description: str | None = None) -> discord.Embed: ...
    def info(...)   ->  ...
    def warning(...) -> ...
    def error(...)  -> ...
    def ticket(kind: TicketKind, ...) -> ...
    def audit(event: AuditEvent) -> ...
```

Единый канон оформления:

| Элемент | Правило |
|---------|---------|
| Цвет | `success` `#43B581`, `info` `#5865F2`, `warning` `#FAA61A`, `error` `#ED4245`, `ticket` `#2B2D31`, `audit` `#9B59B6` |
| Заголовок | `<эмодзи> <Текст>` — эмодзи обязателен |
| Автор | иконка сервера + название площадки |
| Футер | `Stalzone • 31.07.2026 21:45 (GMT+3)` |
| Поля | `inline` для коротких (≤ 3 в ряд), названия с эмодзи, значения — через `format_amount` |
| Разделители | `━━━━━━━━━━━━━━━` между смысловыми блоками |
| Прогресс-бар | `progress.py`: `▰▰▰▰▰▱▱▱▱▱ 52 %` (10 сегментов) |

**Словарь эмодзи** — единая константа `constants.py::Emoji`, чтобы `🪙 Coins` и `⚡ XP` выглядели
одинаково во всех 19 командах и во всех тикетах.

**Лимиты Discord**, зашитые в фабрику с автоматической обрезкой/пагинацией:
title 256, description 4096, field name 256, field value 1024, полей 25, всего 6000, embed'ов 10.

---

### 5.4 `application/services/audit.py` — логи использования команд

Канал: **`1518330495505797143`**.

- Реализуется через `Bot.on_app_command_completion` + `on_app_command_error` → одна точка,
  никакого копипаста в 19 командах.
- Для тикетов (там нет app_command) сервисы явно публикуют `AuditEvent`.
- Формат embed'а строго один:

```
🧾 Использование команды
━━━━━━━━━━━━━━━━━━━━━
👤 Пользователь   @nick (ID: 123…)
📍 Канал          #ticket-0042
⌨️ Команда        /add
📝 Аргументы      тип=Покупка • ник=Scaryyyyy • сумма=299 900 ₽
✅ Результат      Успешно
⏱️ Длительность   0.84 с
🔗 Trace          a1b2c3d4
Stalzone • 31.07.2026 21:45 (GMT+3)
```

- Отправка **не блокирует** ответ пользователю: события кладутся в `asyncio.Queue`,
  отдельный воркер батчит их (до 10 embed'ов в сообщении) и уважает rate limit Discord.
- Если канал логов недоступен — падение в файловый лог, команда всё равно отрабатывает.
- Скриншоты из тикетов уходят в лог **как изображение внутри embed** (`embed.set_image(url=...)`),
  а не вложением — см. §11.5 про срок жизни CDN-ссылок.

---

### 5.5 `presentation/checks.py` — права

```python
def admin_only() -> Callable[[T], T]:
    """Двойная защита: default_permissions скрывает команду в UI,
    runtime-проверка блокирует обход через прямой вызов API."""
```

- Все команды, кроме `/profile` и `/referrals`, помечены `@admin_only()` и имеют
  `default_member_permissions=administrator`.
- В описании команды префикс: `🛡️ [Админ] …`.
- Отказ → эфемерный `error`-embed «Недостаточно прав», плюс запись в аудит.

---

## 6. Модель данных и карта таблицы

### 6.1 Лист `DataBase`

| Блок | Диапазон | Назначение |
|------|----------|-----------|
| Тикеты | `A3:H` | Сделки. Бот **пишет** A,B,C,D,E,H; F,G — формулы |
| Общая база пользователей | `I3:S` | Бот **пишет** только `I` (Discord ID) и `Q` (бустер); остальное — формулы, только чтение |
| Магазин | `U3:V` | Только чтение (списание коинов вручную) |
| item database | `AA3:AG` | Полный CRUD ботом |

Колонки:

```
Тикеты:   A Дата │ B Ник │ C Покупка(✓) │ D Продажа(✓) │ E Сумма │ F Coins* │ G XP* │ H Пришёл от
Юзеры:    I DiscordID │ J Уник.ник* │ K Coins* │ L XP* │ M Оборот покупок* │ N Оборот продаж*
          O Общий оборот* │ P Рефералы* │ Q Бустер │ R Ранг* │ S Роль реферала*
Магазин:  U Ник │ V Потрачено коинов
Предметы: AA id │ AB item name │ AC category │ AD price_buy │ AE price_sell │ AF emoji │ AG updated_at
                                                                        (* — формула)
```

### 6.2 Листы цен (для `/sync_prices`)

Декларативно в `infrastructure/sheets/layouts.py`:

```python
SYNC_LAYOUTS: Final[tuple[SheetLayout, ...]] = (
    SheetLayout(
        sheet="Мейн Скуп", rows=range(1, 32),
        name_columns=("C", "J", "Q", "X", "AE", "AL", "AS"),
        price_columns=("D", "K", "R", "Y", "AF", "AM", "AT"),
        category=ItemCategory.RESOURCE, price_field=PriceField.BUY,
    ),
    SheetLayout(
        sheet="Скуп бустов", rows=range(1, 10),
        name_columns=("C", "J", "Q", "X"),
        price_columns=("D", "K", "R", "Y"),
        category=ItemCategory.RESOURCE, price_field=PriceField.BUY,
    ),
    SheetLayout(
        sheet="БУСТЫ", rows=range(1, 10),
        name_columns=("C", "J", "Q", "X", "AE", "AL", "AS"),
        price_columns=("D", "K", "R", "Y", "AF", "AM", "AT"),
        category=ItemCategory.BOOST, price_field=PriceField.SELL,
    ),
)
```

Добавление нового листа = одна строка в кортеже, никаких правок логики.

### 6.3 Нормализация ников

Формула колонки `J` — `UNIQUE(ARRAYFORMULA(LOWER(B3:B)))`, поэтому:

```python
def normalize_nick(raw: str) -> NormalizedNick:
    """Привести ник к каноническому виду: trim + casefold + схлопывание пробелов."""
```

**Любое** сопоставление ников (профиль, рефералы, статистика, привязка Discord) идёт только по
нормализованной форме. Оригинальное написание хранится отдельно для вывода.

### 6.4 Сущности домена

```python
@dataclass(frozen=True, slots=True)
class TransactionRecord:
    row: int
    at: datetime                 # GMT+3
    nick: NormalizedNick
    nick_display: str
    deal_type: DealType          # PURCHASE (у меня) | SALE (мне)
    amount: Decimal
    coins: int
    xp: int
    referrer: NormalizedNick | None

@dataclass(frozen=True, slots=True)
class UserProfile:
    row: int
    nick: NormalizedNick
    discord_id: int | None
    coins: int
    xp: int
    buy_turnover: Decimal        # M
    sell_turnover: Decimal       # N
    total_turnover: Decimal      # O
    referrals_count: int         # P
    is_booster: bool             # Q
    rank: Rank | None            # R
    referral_role: ReferralRole | None   # S

@dataclass(frozen=True, slots=True)
class Item:
    id: int
    name: str
    category: ItemCategory
    price_buy: Decimal | None
    price_sell: Decimal | None
    emoji: str | None            # имя кастомного эмодзи
    updated_at: datetime | None
    row: int
```

---

## 7. Слой Google Sheets

### 7.1 `client.py`

- Один `gspread.Client` на процесс, ленивая инициализация, service account из `credentials/`.
- **Только батч-операции**: `values_batch_get(ranges)` и `values_batch_update(data)`.
  Правило: одна пользовательская команда → максимум 2 обращения к API.
- `valueRenderOption`:
  - `UNFORMATTED_VALUE` — для чисел (иначе `299 900 ₽` придёт строкой);
  - `FORMULA` — только для диагностики.
- `valueInputOption`:
  - `RAW` — для чисел и текста;
  - `USER_ENTERED` — только при записи формул.
- Все вызовы обёрнуты в `asyncio.to_thread`.

### 7.2 `ratelimit.py`

- Token bucket: 60 read/мин и 60 write/мин на пользователя (лимит Google), с запасом 80 %.
- `asyncio.Lock` на запись в одну и ту же область (защита от гонок при параллельных `/add`).
- Retry с экспоненциальным backoff + jitter на `429`, `500`, `503`; максимум 5 попыток;
  после исчерпания — доменная ошибка `SheetsUnavailableError`, пользователю понятный embed.

### 7.3 ★ Правило: бот НИКОГДА не пишет формулы

**Решение заказчика: формулы в таблице уже настроены и работают, трогать их запрещено.**

Из этого следуют жёсткие правила для всего кода:

- Бот пишет **только** «сырые» ячейки:
  `A, B, C, D, E, H` (Тикеты), `I` и `Q` (Юзеры), `AA:AG` (item database),
  ячейки цен на листах `Мейн Скуп` / `Скуп бустов` / `БУСТЫ`.
- Формульные колонки `F, G, J, K, L, M, N, O, P, R, S` — **read-only**.
  Это зафиксировано константой `READ_ONLY_RANGES` и проверяется в `SheetsClient.batch_update()`:
  попытка записи в защищённый диапазон выбрасывает `ProtectedRangeWriteError` ещё до вызова API.
  Отдельный юнит-тест перебирает все места записи в проекте.
- `valueInputOption=RAW` для всех записей. `USER_ENTERED` не используется нигде —
  это дополнительная гарантия, что строка вида `=...` не превратится в формулу.

> Так как бот не пишет формул, полностью снимается риск `#NAME?` от русских названий функций
> (Sheets API принимает формулы только на английском). Модуль перевода RU→EN не нужен.

**Единственный нюанс — новые строки.** Формулы `F`/`G` должны быть заранее протянуты вниз
на весь рабочий диапазон. Механика:

1. При старте и при каждом синке бот проверяет, до какой строки протянуты `F`/`G`
   и до какой строки идут закреплённые диапазоны формул (сейчас `845`, вы их продлеваете).
2. Если свободных «подготовленных» строк осталось `< 50` — предупреждение в лог-канал:
   `⚠️ Формулы протянуты только до строки N, осталось 43 свободных. Продлите диапазоны.`
3. Если бот всё же пишет сделку в строку без формул `F`/`G`, он **не сочиняет формулу**,
   а копирует её из предыдущей строки серверным запросом
   `spreadsheets.batchUpdate → copyPaste(pasteType=PASTE_FORMULA)`.
   Формула копируется как есть, на языке таблицы, бот её текст даже не видит.
   Это резервный механизм, штатно он срабатывать не должен.

### 7.4 Транзакционность записи

Sheets не даёт транзакций. Стратегия:

1. Найти первую свободную строку в `A:H` (по кэшу, с верификацией одним чтением).
2. Одним `batchUpdate` записать `A, B, C, D, E, H` — **без `F`/`G`**, они считаются формулами.
3. Read-back верификация записанной строки; при расхождении — компенсация (очистка `A–E`, `H`)
   и ошибка пользователю.
4. Read-back с retry (до 3 попыток по 700 мс) для колонок `F`/`G` — дождаться пересчёта
   формул перед показом пользователю начисленных 🪙 / ⚡.
5. Идемпотентность: `idempotency_key` (channel_id + message_id + user_id) хранится в SQLite;
   повторное нажатие «Подтвердить» не создаёт дубль.

### 7.5 `/del_item` — сдвиг блока предметов

Нельзя удалить строку листа целиком (в этих же строках лежат Тикеты и Юзеры).
Алгоритм: прочитать `AA3:AG{last}`, убрать элемент из списка в памяти, перенумеровать `id`,
записать весь блок обратно одним `batchUpdate` + очистить освободившуюся последнюю строку.
Операция под `asyncio.Lock`, с бэкапом прежнего состояния в SQLite на случай сбоя.

---

## 8. Слой кэша (SQLite)

### 8.1 Схема

```sql
CREATE TABLE items (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_norm TEXT NOT NULL,
    category TEXT NOT NULL, price_buy TEXT, price_sell TEXT,
    emoji TEXT, updated_at TEXT, sheet_row INTEGER NOT NULL);
CREATE UNIQUE INDEX ix_items_name_cat ON items(name_norm, category);

CREATE TABLE users (
    nick_norm TEXT PRIMARY KEY, nick_display TEXT NOT NULL, discord_id INTEGER,
    coins INTEGER NOT NULL DEFAULT 0, xp INTEGER NOT NULL DEFAULT 0,
    buy_turnover TEXT, sell_turnover TEXT, total_turnover TEXT,
    referrals_count INTEGER NOT NULL DEFAULT 0, is_booster INTEGER NOT NULL DEFAULT 0,
    rank TEXT, referral_role TEXT, sheet_row INTEGER NOT NULL, synced_at TEXT NOT NULL);
CREATE INDEX ix_users_discord ON users(discord_id);

CREATE TABLE transactions (
    sheet_row INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL,
    nick_norm TEXT NOT NULL, deal_type TEXT NOT NULL, amount TEXT NOT NULL,
    coins INTEGER, xp INTEGER, referrer_norm TEXT);
CREATE INDEX ix_tx_date ON transactions(occurred_at);
CREATE INDEX ix_tx_nick ON transactions(nick_norm);

CREATE TABLE progression_state (          -- для детекта повышений
    nick_norm TEXT PRIMARY KEY, last_rank TEXT, last_referral_role TEXT,
    announced_at TEXT);

CREATE TABLE ticket_sessions (            -- persistent views
    channel_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, author_id INTEGER NOT NULL,
    status TEXT NOT NULL, delivery_method TEXT, game_nick TEXT,
    referrer_nick TEXT, referrer_discord_id INTEGER, deadline TEXT,
    screenshot_url TEXT, screenshot_message_id INTEGER,
    summary_message_id INTEGER, panel_message_id INTEGER,
    ocr_status TEXT NOT NULL DEFAULT 'disabled',   -- ★ disabled|pending|done|failed
    ocr_analysis_id INTEGER,                       -- ★ → screenshot_analyses.id
    idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE boost_order_lines (
    channel_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
    item_name_norm TEXT NOT NULL, category TEXT NOT NULL,   -- страховка от перенумерации id
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (channel_id, item_id));

-- ★ ЗАДЕЛ ПОД OCR (M13). Таблица создаётся с первого дня; в v1.0 пишется только
-- строка со статусом и путём к сохранённому образцу — этого достаточно, чтобы к моменту
-- разработки OCR уже был накоплен реальный датасет скриншотов.
CREATE TABLE screenshot_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    image_sha256 TEXT NOT NULL,
    image_url TEXT,                       -- постоянная ссылка из лог-канала
    sample_path TEXT,                     -- data/ocr_samples/<sha>.png, если включён сбор
    width INTEGER, height INTEGER, size_bytes INTEGER, mime TEXT,
    engine TEXT,                          -- tesseract | paddle | vision | null
    status TEXT NOT NULL,                 -- pending | done | failed | skipped
    raw_text TEXT,                        -- полный распознанный текст
    items_json TEXT,                      -- сериализованный tuple[RecognizedItem, ...]
    total_estimate TEXT,                  -- Decimal как строка
    confidence REAL,
    duration_ms INTEGER,
    error TEXT,
    created_at TEXT NOT NULL);
CREATE INDEX ix_shots_channel ON screenshot_analyses(channel_id);
CREATE UNIQUE INDEX ix_shots_sha ON screenshot_analyses(image_sha256);

CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

> Денежные значения хранятся строками (`TEXT`) и восстанавливаются в `Decimal` —
> `REAL` в SQLite потерял бы точность на суммах в сотни миллионов.

### 8.2 Стратегия синхронизации

| Данные | Полный рефреш | Точечный рефреш |
|--------|---------------|-----------------|
| `items` | каждые 10 мин | после `/item_add`, `/del_item`, `/setprice`, `/setboost`, `/new_price` |
| `users` | каждые 3 мин | после `/add`, подтверждения тикета, `/set_referral`, `/set_rank` |
| `transactions` | каждые 3 мин (инкрементально, от последней известной строки) | после `/add` |

- Чтение из кэша сопровождается проверкой `synced_at`; если данные старше `STALE_AFTER`,
  выполняется inline-рефреш перед ответом (с `interaction.response.defer()`).
- Все три таблицы читаются **одним** `values_batch_get` — это 1 запрос к API на цикл.
- `sync_meta` хранит `last_full_sync`, `last_tx_row`, версию схемы.
- При старте бота — обязательная полная синхронизация до регистрации команд.

---

## 9. Домен прогрессии: ранги, рефералы, бонусы

### 9.1 Лестницы (декларативно, `domain/progression/`)

```python
@dataclass(frozen=True, slots=True)
class RankTier:
    key: str
    label: str                # "👑 Legend" — ровно как в колонке R
    role_id: int
    xp_required: int
    perks: tuple[str, ...]    # готовые строки для embed'а

RANKS: Final = (
    RankTier("standard", "🔹 Standard", 1518324856549277827,   50, (...)),
    RankTier("premium",  "🔷 Premium",  1518328036137631805,  300, (...)),
    RankTier("prestige", "💠 Prestige", 1518328037631066232, 1200, (...)),
    RankTier("elite",    "💎 Elite",    1518328222939611166, 3500, (...)),
    RankTier("legend",   "👑 Legend",   1518328324605083698, 7000, (...)),
)
```

```python
REFERRAL_ROLES: Final = (
    ReferralTier("scout",      "🧭 Скаут",             1518583879672270878,  1, (...)),
    ReferralTier("promoter",   "📣 Промоутер",         1518584176054636584,  3, (...)),
    ReferralTier("recruiter",  "🧲 Вербовщик",         1518584268933300274,  7, (...)),
    ReferralTier("ambassador", "📢 Амбассадор",        1518584424818671687, 20, (...)),
    ReferralTier("baron",      "🎩 Рекламный Барон",   1518584494410563625, 50, (...)),
)

PARTNER_ROLE_ID: Final = 1518584570457358556   # 🤝 Партнёр — 3-й этап реферальной системы
```

`RankLadder` предоставляет: `current(xp)`, `next(xp)`, `progress(xp) -> Progress(done, need, pct)`,
`perks_of(tier)`. Всё чисто, полностью покрыто юнит-тестами.

> **Важно (решение A2):** пороги в коде **не считают** ранг — ранг берётся из колонки `R`.
> Лестница нужна только для UI: прогресс-бары «До 💎 Elite», списки бонусов, награда следующей
> роли. Тест `test_ladder_matches_sheet_formula` сверяет пороги в коде с порогами в формуле,
> чтобы расхождение было поймано в CI, а не в проде.

### 9.1.1 ★ Канонические числа (решение A6: истина — формулы)

Текстовое описание системы расходилось с формулами в 10 местах. **Принято: везде формулы.**
Тексты бонусов в embed'ах приводятся к этим значениям — ниже эталон, от которого пишется
`perks.py`. Старые значения из описания в интерфейсе не появляются нигде.

| Параметр | ❌ Было в описании | ✅ Канон (формула) |
|----------|-------------------|--------------------|
| 🪙 1 Coin + ⚡ 10 XP за оборот **покупок** | 1 000 000 ₽ | **1 500 000 ₽** |
| 🪙 1 Coin + ⚡ 10 XP за оборот **продаж** | 2 500 000 ₽ | 2 500 000 ₽ *(совпадает)* |
| 🔹 Standard | 50 XP | 50 XP *(совпадает)* |
| 🔷 Premium | 250 XP | **300 XP** |
| 💠 Prestige | 1 000 XP | **1 200 XP** |
| 💎 Elite | 5 000 XP | **3 500 XP** |
| 👑 Legend | 10 000 XP | **7 000 XP** |
| 🧭 Скаут | 1 друг | 1 друг *(совпадает)* |
| 📣 Промоутер | 5 друзей | **3 друга** |
| 🧲 Вербовщик | 10 друзей | **7 друзей** |
| 📢 Амбассадор | 25 друзей | **20 друзей** |
| 🎩 Рекламный Барон | 100 друзей | **50 друзей** |
| 🚀 Буст сервера, разово | 🪙 3 + ⚡ 60 | 🪙 3 + **⚡ 30** |
| ⚡ Бонус +5 % к XP | «при ранге Premium» | **при базовом XP ≥ 250** (до ранга Premium) |
| Реферальные этапы 2 и 3 для пригласившего | по обороту **каждого** друга | по **суммарному обороту всех** приглашённых (`СУММЕСЛИ(H; ник; O)`) |

Реферальные бонусы (формула, приводится в UI как есть):

| Этап | Порог (оборот покупок / продаж) | Рефералу | Пригласившему |
|------|--------------------------------|----------|---------------|
| 1 | 1 500 000 / 2 500 000 ₽ | 🪙 2 + ⚡ 10 | 🪙 3 + ⚡ 20 |
| 2 | 5 000 000 / 12 500 000 ₽ | 🪙 3 + ⚡ 40 | 🪙 2 + ⚡ 20 |
| 3 | 50 000 000 / 125 000 000 ₽ | 🪙 15 + ⚡ 200 | 🪙 10 + ⚡ 80 + роль 🤝 Партнёр |

Разовые бонусы за ранги: Standard 🪙 5 · Premium 🪙 10 · Prestige 🪙 40 · Elite 🪙 100 · Legend 🪙 200.
Разовые бонусы за реф-роли: Скаут 🪙 1 · Промоутер 🪙 5 + ⚡ 10 · Вербовщик 🪙 15 ·
Амбассадор 🪙 40 + ⚡ 60 · Барон 🪙 150.

**Ограничение диапазонов.** Формулы закреплены до строки `845`; заказчик продлевает их вручную.
Бот при каждом синке считает остаток свободных строк и предупреждает в лог-канал при `< 50`.

### 9.2 `ProgressionService` — выдача ролей и поздравления

```python
async def sync(self, nicks: Collection[NormalizedNick] | None = None) -> list[Promotion]:
    """Сверить ранг/реф-роль игроков с Discord-ролями, выдать недостающие,
    снять устаревшие и вернуть список фактических повышений."""
```

Алгоритм:

1. Прочитать профили (кэш) для указанных ников или для всех (фоновый режим).
2. Сравнить `rank` / `referral_role` с `progression_state.last_*`.
3. Если повышение → `RoleGateway.sync_roles()`: выдать новую роль, снять предыдущие
   роли той же лестницы (ранги взаимоисключающие, реф-роли тоже).
4. Записать новое состояние в `progression_state` **до** отправки сообщения
   (чтобы при падении не заспамить повторным поздравлением).
5. Отправить публичное поздравление **в канал, где произошло событие** (см. ниже).
6. Отправить событие в аудит-канал.

**Куда идёт поздравление** (отдельного канала анонсов нет — решение заказчика):

| Триггер | Канал поздравления |
|---------|--------------------|
| `/add` | канал, где вызвана команда |
| Подтверждение тикета | канал тикета |
| `/set_referral`, `/set_rank` | канал, где вызвана команда |
| Фоновый поллер (нет контекста канала) | лог-канал `1518330495505797143` |

Сигнатура отражает это явно, без «магии»:

```python
async def sync(
    self,
    nicks: Collection[NormalizedNick] | None = None,
    *,
    announce_to: discord.abc.Messageable | None = None,   # None → лог-канал
) -> list[Promotion]: ...
```

Триггеры: после `/add`, после подтверждения тикета, после `/set_referral` / `/set_rank`,
`tasks.loop(minutes=5)` по всей базе, `on_member_update`
(детект буста сервера → запись флага в колонку `Q`).

Публичное поздравление:

```
🎉 Повышение!
@Игрок поднялся до 💎 Elite!

📊 Сейчас: 🪙 1 240 Coins • ⚡ 3 780 XP
🎁 Разовый бонус: 🪙 100 Coins
🔥 Крупные сделки: 🪙 5 Coins за сделку свыше 100 000 000 ₽
📊 Торговля: скидка 3 % / наценка 1.5 %
⏱️ Привилегия: приоритет в очереди + бронь товара
```

---

## 10. Спецификация команд

Общие правила для всех 19 команд:

- `description` начинается с эмодзи; у админских — префикс `🛡️ [Админ]`.
- Все ответы, помеченные «только автору», используют `ephemeral=True`.
- Долгие операции: `await interaction.response.defer(ephemeral=...)` в первые 3 секунды.
- Ошибки → единый `error`-embed + запись в аудит + `trace_id` для поиска в логах.
- Денежные аргументы принимаются **строкой** и проходят через `parse_amount` /
  `evaluate_amount` — чтобы `299 900 ₽` и `299900+10000` работали одинаково.

| # | Команда | Права | Аргументы |
|---|---------|-------|-----------|
| 1 | `/add` | Админ | `тип` (choice: Покупка/Продажа), `ник` (str), `discord` (Member), `сумма` (str), `реферал_ник` (str, опц.), `реферал_discord` (Member, опц.) |
| 2 | `/profile` | Все | `ник` (str) |
| 3 | `/referrals` | Все | `ник` (str) |
| 4 | `/price_list` | Админ | — |
| 5 | `/give_price` | Админ | — |
| 6 | `/new_price` | Админ | `файл` (Attachment) |
| 7 | `/setprice` | Админ | `предмет` (autocomplete, category=resource), `цена` (str) |
| 8 | `/setboost` | Админ | `буст` (autocomplete, category=boost), `цена` (str) |
| 9 | `/sync_prices` | Админ | — |
| 10 | `/item_add` | Админ | `название`, `категория` (choice), `цена_покупки` (опц.), `цена_продажи` (опц.), `эмодзи` (опц.) |
| 11 | `/del_item` | Админ | `название` (autocomplete), `категория` (опц., показывается при дубле) |
| 12 | `/logs` | Админ | — |
| 13 | `/day` | Админ | `дата` (str `ДД.ММ.ГГГГ`) |
| 14 | `/week` | Админ | `начало`, `конец` |
| 15 | `/month` | Админ | `месяц` (choice 1–12), `год` (int) |
| 16 | `/set_referral` | Админ | `ник`, `discord` (Member), `ник_пригласившего`, `discord_пригласившего` (Member) |
| 17 | `/set_rank` | Админ | `ник`, `discord` (Member), `ранг` (choice) |
| 18 | `/tag` | Админ | `пользователь` (Member) |

### 10.1 `/add` — фиксация сделки

**Флоу:**

1. `defer(ephemeral=True)`.
2. `evaluate_amount(сумма)` → `Decimal`. Ошибка → embed с примерами корректного ввода.
3. `normalize_nick` для ника игрока и реферала.
4. Валидации:
   - реферал ≠ сам игрок;
   - если указан `реферал_ник`, но не `реферал_discord` — предупреждение (не блокирует);
   - если `ник` уже привязан к другому Discord ID → предупреждение админу с подтверждением.
5. Запись строки в `Тикеты` (§7.4): дата = `now()` GMT+3, флаг `C` или `D` = `TRUE`.
   Колонки `F`/`G` не трогаются — их считают формулы.
6. Привязка `Discord ID` в колонку `I` напротив ника (если ещё не привязан).
7. Точечный рефреш кэша → `ProgressionService.sync([ник, реферал], announce_to=interaction.channel)`
   — поздравление о повышении, если оно случилось, придёт в этот же канал.
8. **Эфемерный embed автору** — «Сделка зафиксирована»: тип, ник, Discord, сумма,
   начисленные 🪙/⚡, реферал, дата/время, номер строки.
9. **Публичное сообщение** в канале — завершение сделки + напоминание про отзыв
   со ссылкой на канал `1490342809075716237`.
10. Аудит-событие.

### 10.2 `/profile`

- Проверка привязки: `UserRepository.get_by_nick(ник)` → если `discord_id` пуст или
  ≠ `interaction.user.id` → отказ «Вы можете смотреть только свой профиль».
  Админам разрешено смотреть чужие (флаг в конфиге).
- Embed (эфемерный):

```
👤 Профиль — Scaryyyyy
━━━━━━━━━━━━━━━━━━━━━
🪙 Coins    1 240        ⚡ XP    3 780       🏅 Ранг    💎 Elite
🤝 Реф-роль 🧲 Вербовщик  👥 Приглашено  12
━━━━━━━━━━━━━━━━━━━━━
🎁 Бонусы ранга 💎 Elite
 • 🪙 100 Coins при достижении
 • 🔥 5 Coins за сделку свыше 100 000 000 ₽
 • 📊 Скидка 3 % / наценка 1.5 %
 • ⏱️ Приоритет в очереди + бронь товара
━━━━━━━━━━━━━━━━━━━━━
📈 До 👑 Legend
▰▰▰▰▰▰▰▱▱▱  3 780 / 7 000 XP  (54 %)
```

### 10.3 `/referrals`

Та же проверка привязки. Embed:

- 🤝 Реф-роль + её бонусы;
- 👥 Приглашено (N) — список: `игровой ник → @discord_tag` (пагинация по 15, если больше);
- 📈 прогресс-бар до следующей реф-роли;
- 🎁 награда следующей роли.

### 10.4 `/price_list`

- Эфемерный embed + `View` с двумя кнопками-переключателями: `📦 Цены на ресурсы` (по умолчанию)
  и `🚀 Цены на бусты`. Активная кнопка `disabled` + стиль `primary`.
- Строки: `<эмодзи> **Название**` / `└ Скуп: 250 000 ₽ • Продажа: 300 000 ₽`.
- Если позиций больше, чем помещается в лимиты embed — внутренняя пагинация (`◀ 1/3 ▶`).
- `timeout=180`, по таймауту кнопки отключаются.

### 10.5 `/give_price`

Выгружает всю item database в TXT (`discord.File`, in-memory `BytesIO`, UTF-8 BOM для Windows).
**Формат жёстко зафиксирован**, т. к. `/new_price` его же и парсит:

```
# Прайс-лист Stalzone — выгружено 31.07.2026 21:45 (GMT+3)
# Меняйте ТОЛЬКО колонки "Скуп" и "Продажа". Строки со знаком # игнорируются.
# Формат числа: 250000, 250 000 или 250к — всё будет понято корректно.
# Пустое значение = цены нет.
#
ID  | Название            | Категория | Скуп      | Продажа   | Эмодзи   | Обновлено
----+---------------------+-----------+-----------+-----------+----------+-----------------
1   | Топот               | boost     |           | 300 000   | topot    | 30.07.2026 12:00
2   | Топот               | resource  | 250 000   |           | topot    | 30.07.2026 12:00
3   | Хвост тушкана       | resource  | 18 000    |           | tail     | 29.07.2026 09:10
```

### 10.6 `/new_price`

1. Валидация вложения: расширение `.txt`, размер ≤ 1 МБ, кодировка UTF-8/CP1251 (автодетект).
2. Парсинг: пропуск `#` и строк-разделителей, split по `|`, trim, `parse_amount` для цен.
3. Сопоставление: по `ID` (первично) → fallback `название + категория`.
4. **Валидация до применения**: собрать все ошибки (неизвестный ID, битое число,
   отрицательная цена, дубль строки) и, если они есть, показать отчёт и **ничего не менять**.
5. Показать diff и кнопки `✅ Применить` / `❌ Отмена`
   *(включено по умолчанию; отключается флагом `PRICE_IMPORT_CONFIRM=false` — §17.3, п. 1)*.
6. Применить: один `batchUpdate` по колонкам `AD`/`AE` + `AG` (updated_at) → рефреш кэша.
7. Опционально сразу запустить `/sync_prices` (флаг в конфиге).
8. Эфемерный отчёт по требуемой форме:

```
✏️ Изменение цен на ресурсы:
 • 🪙Хвост тушкана | 18 000 ₽ → 19 500 ₽
 • Кристалл | 120 000 ₽ → 125 000 ₽

✏️ Изменение цен на бусты:
 • 🚀Топот | 300 000 ₽ → 310 000 ₽

✏️ Изменение цен на скуп бустов:
 • 🚀Топот | 250 000 ₽ → 260 000 ₽
```

> «Скуп бустов» = позиция с `category=resource`, чьё название совпадает с названием
> позиции `category=boost`. Определяется автоматически, отдельным блоком, через пустую строку.

### 10.7 `/setprice` / `/setboost`

- Autocomplete из SQLite: подстрочный + fuzzy-поиск, лимит 25, показывает эмодзи и текущую цену.
- Значение опции — `item_id` (не название), чтобы не ломаться на дублях имён.
- Цена через `evaluate_amount` (можно вводить `250к` или `240000+10000`).
- Вывод — тот же рендерер отчёта, что у `/new_price` (общий код, `PriceChangeReport`).

### 10.8 `/sync_prices`

1. Один `values_batch_get` по всем `SYNC_LAYOUTS` → все имена и текущие цены.
2. Для каждой ячейки: `normalize_nick`-подобная нормализация имени → поиск в item database
   по `(name_norm, category_листа)`.
3. Один `values_batch_update` — только реально изменившиеся ячейки.
4. Отчёт: `✅ Обновлено N` / `⚠️ Не найдено в базе: …` / `➖ Без изменений: N`.
5. Идемпотентность: повторный запуск без изменений даёт 0 записей.

### 10.9 `/item_add` / `/del_item`

- `/item_add`: `id = max(id) + 1`, запись в первую свободную строку `AA:AG`,
  `updated_at = now()`. Проверка дубля `(название, категория)` → отказ.
  Проверка существования эмодзи на сервере → предупреждение, если не найден.
- `/del_item`: autocomplete по всем предметам; аргумент `категория` показывается,
  когда имя встречается в обеих категориях. Удаление + сдвиг блока вверх (§7.5).
  **ID перенумеровываются** (решение заказчика): после удаления `id` идут подряд `1…N`
  и совпадают с порядковым номером — «дырок» в нумерации нет.
  Следствие, которое учитываем в коде: `id` **нестабилен**, поэтому нигде не хранится
  как долгоживущая ссылка. В `boost_order_lines` вместе с `item_id` пишется
  `item_name_norm` + `category`; при рассинхроне после `/del_item` позиция переразрешается
  по имени, а если предмет действительно удалён — убирается из черновика заказа
  с уведомлением автору.
  Embed подтверждения со всеми полями удалённого предмета (на случай отката).

### 10.10 `/logs` — архив сделок

- Источник: `transactions` (кэш), сортировка по дате ↓.
- **Номер сделки** пересчитывается внутри суток: первая сделка после `00:00 GMT+3` = `1`.
  Реализация — оконная функция `ROW_NUMBER() OVER (PARTITION BY date(occurred_at) ORDER BY sheet_row)`.
- Пагинация: `View` с `⏮ ◀ 3/17 ▶ ⏭` + кнопка `🔢 К странице` (модал с номером).
  25 строк на страницу, моноширинный блок для выравнивания колонок.
- Формат строки: `#12 │ 31.07.2026 21:45 │ Scaryyyyy │ @nick │ 🟢 Покупка │ 299 900 ₽`.
- `timeout=300`, привязка View к автору (чужие нажатия отклоняются).

### 10.11 `/day` / `/week` / `/month`

Общий сервис `StatsService.report(period: DateRange) -> PeriodReport`, три тонкие обёртки.

Embed:

```
📊 Статистика за 31.07.2026
━━━━━━━━━━━━━━━━━━━━━
Ник           │ Discord   │ 🟢 Покупка  │ 🟡 Продажа  │ 🔄 Оборот
Scaryyyyy     │ @scary    │ 5 000 000   │ —           │ 5 000 000
…
━━━━━━━━━━━━━━━━━━━━━
🟢 Всего покупок (у меня):   28 500 000 ₽
🟡 Всего продаж (мне):       12 300 000 ₽
💰 Чистая прибыль:           16 200 000 ₽
🧾 Сделок:                   14
```

- **«Чистая прибыль» = `Σ покупок (у меня) − Σ продаж (мне)`** (подтверждено заказчиком) —
  чистый денежный поток за период. Отрицательное значение выводится красным маркером `🔻`.
- Список игроков сортируется по обороту ↓, при > 20 игроках включается пагинация.
- Валидация периода: `/week` — конец ≥ начала и диапазон ≤ 31 дня; будущие даты запрещены.

### 10.12 `/set_referral` / `/set_rank`

**`/set_referral`** — запись ника пригласившего в колонку `H` **только первой (самой ранней)
строки игрока** в таблице «Тикеты». Так `СЧЁТЕСЛИ` в колонке `P` засчитает ровно одного
реферала, а не по одному на каждую сделку.

- Поиск первой строки: `MIN(sheet_row)` по `nick_norm` в кэше, с верификацией чтением.
- Если у игрока ещё нет ни одной сделки → отказ с понятным текстом
  («реферала можно указать только после первой сделки игрока»).
- Если в колонке `H` этой строки уже стоит другой реферал → предупреждение с кнопкой
  подтверждения перезаписи.
- Привязка Discord ID обоих игроков в колонку `I`.
- Вызов `ProgressionService.sync([игрок, пригласивший], announce_to=interaction.channel)`.

**`/set_rank`** — ранг вычисляется формулой из XP и в таблице не меняется.
Ручная выдача = **выдача только Discord-роли**.

- Аргументы: `ник`, `discord` (Member), `ранг` (choice из 5 рангов).
- Таблица **не изменяется** вообще.
- Флаг `manual_rank_role` записывается в `progression_state`, чтобы фоновый поллер
  не снял выданную роль при следующей сверке.
- Снятие ручной выдачи: повторный вызов с тем же рангом снимает роль и флаг (toggle).

**Оба:** эфемерный embed с введёнными данными + публичное сообщение в текущем канале
с тегами обоих пользователей, игровыми никами и пометкой
«Выдано вручную администратором @admin».

### 10.13 `/tag`

- Отправляет пользователю **личное сообщение** (DM) с embed'ом «🔔 Уведомление по тикету»:
  приветствие, название тикета, просьба зайти и ответить, когда будет время.
- Кнопка `🎫 Перейти к тикету` — `discord.ui.Button(style=discord.ButtonStyle.link, url=...)`,
  ссылка вида `https://discord.com/channels/{guild_id}/{channel_id}`.
  Кнопка серая — link-кнопки в Discord зелёными быть не могут (принято заказчиком).
- Если DM закрыты → фолбэк: пинг в канале тикета + эфемерное предупреждение админу.

---

## 11. Система тикетов

### 11.1 Отслеживаемые категории

```python
TICKET_CATEGORIES: Final[Mapping[int, TicketKind]] = {
    1475149130748657841: TicketKind.SELL_ITEMS,    # Заявки на продажу
    1503802805801058336: TicketKind.SELL_BOOSTS,   # Заявки на продажу бустов
    1479228622014251049: TicketKind.ORDER_BOOSTS,  # Заявки на заказ бустов
}
TICKET_TOOL_BOT_ID: Final = 557628352828014614
```

### 11.2 Появление панели заявки

`on_guild_channel_create` → если `channel.category_id in TICKET_CATEGORIES`:

1. Создать запись в `ticket_sessions` (`status=AWAITING_TOOL`).
2. **Не спать фиксированное время**, а слушать `on_message`: как только Ticket Tool
   (`557628352828014614`) пишет в этот канал — публиковать панель.
   Фолбэк-таймер 30 с: если Ticket Tool молчит, публиковать всё равно.
3. Панель = embed по типу канала (`Заявка на продажу предметов` / `…бустов` /
   `Заявка на заказ бустов`) + описание «чтобы оформить сделку, заполните форму по кнопке ниже»
   + **persistent** кнопка `📝 Заполнить заявку` (`custom_id=f"ticket:start:{kind}"`).

### 11.3 Выбор способа передачи

Нажатие кнопки → эфемерное сообщение:

```
📮 Выберите способ передачи
Ник: Scaryyyyy
Отправлять предметы / деньги на этот ник при выборе «Почта».
```

`Select` с опциями `📬 Почта` / `🤝 Обмен`. Выбор сохраняется в `ticket_sessions`,
затем открывается модал.

### 11.4 Модальные окна

| Тип тикета | Поля |
|-----------|------|
| Продажа предметов | Ваш игровой ник • Кто пригласил (в игре, *необязательно*) • Кто пригласил (Discord, *необязательно*) |
| Продажа бустов | то же |
| Заказ бустов | Ваш игровой ник • До какой даты и времени (`ДД.ММ.ГГГГ ЧЧ:ММ`, GMT+3) • Кто пригласил (в игре) • Кто пригласил (Discord) |

- Discord **не имеет** date-picker в модалах — только текстовое поле (принято заказчиком).
  Реализация: `label="До какой даты и времени нужно сделать"`,
  `placeholder="31.07.2026 21:00 (по МСК)"`, устойчивый парсер `parse_deadline()`.
  Принимаемые формы: `31.07.2026 21:00`, `31.07.26 21:00`, `31.07 21:00` (год текущий),
  `31.07.2026` (время → `23:59`), `завтра 20:00`, `через 3 часа`, `сегодня 22:30`.
  Разделители `.`, `/`, `-` равнозначны. Валидация: дата в будущем и не далее чем на 90 дней;
  при ошибке модал переоткрывается с подсказкой и сохранёнными остальными полями.
- Незаполненные поля реферала **не выводятся** в итоговом embed'е.
- Ник пригласившего в Discord вводится текстом (модал не поддерживает user-picker);
  бот пытается сопоставить строку с участником сервера и подставить нормальный тег.

### 11.5 Итоговая заявка (продажа предметов / бустов)

Одно **публичное** сообщение-карточка, которое **редактируется на месте** (не пересоздаётся):

```
🎫 Заявка на продажу предметов
━━━━━━━━━━━━━━━━━━━━━
👤 Игрок          @user
🎮 Игровой ник     Scaryyyyy
📮 Способ          📬 Почта
🤝 Пригласил       OtherNick (@other)
🕒 Создана         31.07.2026 21:45 (GMT+3)
[изображение скриншота]
```

Кнопки: `📸 Прикрепить скриншот` (все) • `✅ Подтвердить` (только админ).

- `📸` → эфемерное сообщение с требованиями к скриншоту (полный экран, читаемые цифры,
  без обрезки интерфейса, PNG/JPG, ≤ 8 МБ) и кнопкой открытия модала/приёма вложения.
  Полученное изображение вставляется в карточку через `embed.set_image`.
- `✅ Подтвердить` → модал `💰 Сумма сделки` (одно поле, `evaluate_amount`) →
  далее ровно тот же путь, что и `/add` (общий `TransactionService.register()`),
  все остальные данные берутся из `ticket_sessions`.
- Затем `ProgressionService.sync([...], announce_to=ticket_channel)` — поздравление
  о повышении приходит прямо в канал тикета.

#### ★ Скриншот в логах — без отдельного архивного канала

Отдельного канала-архива нет (решение заказчика), всё уходит **в лог-канал**.
Наивный вариант «положить в embed ссылку на вложение из тикета» сломается: Discord отдаёт
вложения по подписанным URL с ограниченным сроком, и через сутки картинка в логах исчезнет.

Рабочее решение — **лог-канал сам становится архивом**:

```python
data = await attachment.read()                       # байты скриншота
file = discord.File(io.BytesIO(data), filename="screenshot.png")
embed.set_image(url="attachment://screenshot.png")   # картинка ВНУТРИ embed
await log_channel.send(embed=embed, file=file)
```

Что это даёт:

- Изображение рендерится **внутри embed**, а не отдельным блоком вложения —
  ровно как вы просили («ембед такое же в логи со скрином, не прикреплённое файлом»).
- Ссылка принадлежит сообщению лог-канала и живёт столько же, сколько само сообщение,
  — не протухает.
- Та же техника применяется к карточке в канале тикета:
  `message.edit(embed=embed, attachments=[file])`, поэтому и там картинка вечная.
- В `ticket_sessions.screenshot_url` хранится ссылка из **лог-канала** (постоянная),
  а не из исходного вложения.
- Ограничения: размер ≤ 8 МБ (лимит загрузки без Nitro), форматы PNG / JPG / WEBP.
  Слишком большой файл → эфемерное предупреждение с просьбой сжать.
- ★ Те же скачанные байты передаются в `ScreenshotService.on_attached()`: считается
  `sha256`, пишется строка в `screenshot_analyses`, при `OCR_KEEP_SAMPLES=true` образец
  ложится в `data/ocr_samples/`, затем вызывается `OcrGateway.recognize()`
  (в v1.0 — `NullOcrGateway`, результат пустой). Подробности — §11.8.

### 11.6 Заказ бустов — редактор заказа

Требование «бот должен удалять прошлые сообщения, чтобы осталась только итоговая заявка»
реализуется сильнее: **одно сообщение-редактор, которое редактируется in-place**.
Ничего не пересоздаётся → нечего удалять, нет мигания и гонок.

Состояние (`boost_order_lines`) в SQLite, поэтому переживает рестарт.

Компоненты одного сообщения:

```
🧾 Редактор заказа
━━━━━━━━━━━━━━━━━━━━━
Ваш заказ:
 🚀 Топот      × 3   =   930 000 ₽
 ⚡ Ускорение  × 1   =   150 000 ₽
━━━━━━━━━━━━━━━━━━━━━
💰 Итого:  1 080 000 ₽
⏳ Срок:   02.08.2026 20:00 (GMT+3)

[Select] Выберите буст для изменения количества ▾
[➖] [➕] [🔢 Ввести количество] [🗑️ Удалить]
[➕ Добавить бусты]  [✅ Подтвердить заказ]
```

- `➕ Добавить бусты` → мультиселект со всеми бустами из базы; уже выбранные помечены
  галочкой и показывают текущее количество (`✅ 🚀 Топот — 3 шт.`).
- **Лимит Discord: 25 опций в select → постраничный выбор** (принято заказчиком).
  Реализация `PaginatedItemSelect`: сортировка по названию, 25 позиций на страницу,
  кнопки `◀ Страница 1/3 ▶` под селектом. Выбор сохраняется при переключении страниц
  (состояние в `boost_order_lines`, не в памяти View), в заголовке видно
  `Выбрано: 4 позиции`. Тот же компонент переиспользуется в `/del_item` и `/price_list`.
- `🔢 Ввести количество` → модал с одним полем; значение проходит через `parse_amount`
  (чтобы `1 000` тоже понималось), проверка `1 ≤ qty ≤ 9999`.
- Цена берётся из item database (`price_sell` для бустов) на момент **подтверждения**,
  а не выбора — чтобы заказ не «застревал» на старой цене. Изменение цены между шагами
  подсвечивается в карточке.
- `✅ Подтвердить заказ` (админ) → модал суммы, предзаполненный расчётной суммой →
  сделка типа **Покупка (у меня)** через общий `TransactionService.register()`.
- Скриншот в этом типе тикета не требуется.
- Взаимодействовать с редактором может только автор заявки (+ админы).

### 11.7 Persistent views

Все `custom_id` детерминированы (`ticket:start:sell_items`, `ticket:confirm`,
`order:qty:plus`, …). При старте бот регистрирует View-классы через `bot.add_view()`
и восстанавливает состояние из `ticket_sessions` — кнопки на старых сообщениях живут вечно.

### 11.8 ★ Задел под OCR скриншотов (реализация — этап M13)

**Цель будущей фичи.** Игрок прикрепляет скриншот инвентаря / обмена → бот распознаёт
названия предметов и количества → сопоставляет с item database → считает предварительную
сумму → **предзаполняет** админский модал «Сумма» и подсвечивает расхождения.
OCR **никогда не пишет в таблицу сам** — он только предлагает, финальное слово за админом.

#### Что делается в v1.0, чтобы M13 «прикрутился» без переписывания

| Задел | Где | Зачем |
|-------|-----|-------|
| Порт `OcrGateway` + `NullOcrGateway` | `application/ports/ocr.py`, `infrastructure/ocr/null.py` | Тикеты уже вызывают OCR, просто получают пустой результат. Включение = замена одной строки в `bootstrap.py` |
| Доменные DTO `ScreenshotImage`, `OcrResult`, `RecognizedItem` | `domain/entities/screenshot.py` | Контракт зафиксирован заранее, движки под него подстраиваются |
| Таблица `screenshot_analyses` + колонки `ocr_status` / `ocr_analysis_id` | `cache/schema.sql` | Схема БД не меняется при включении OCR |
| ★ **Сбор датасета образцов** | `infrastructure/ocr/samples.py`, флаг `OCR_KEEP_SAMPLES=true` | **Самое важное.** Работает с M9 — каждый скриншот из тикетов сохраняется в `data/ocr_samples/` |
| Событие `ScreenshotAttached` | `application/services/screenshots.py` | Точка расширения: сейчас только сохраняет, потом ещё и распознаёт |
| Функция `render_ticket_card(session)` | `presentation/cogs/tickets/` | Карточка строится из состояния, а не «по месту». Добавление OCR-блока не трогает флоу |
| Модал суммы принимает `default` | `presentation/modals/` | Предзаполнение расчётной суммой — параметр уже есть, в v1.0 просто пустой |

> **Почему сбор датасета критичен.** OCR по игровым скриншотам настраивается эмпирически:
> шрифт Stalcraft, цветной фон, сжатие Discord. Без пары сотен реальных примеров подобрать
> препроцессинг и порог фаззи-матчинга невозможно. Если начать копить с первого дня работы
> тикетов, к моменту M13 будет готовая выборка — иначе разработка OCR встанет на месяц
> ожидания данных. Флаг `OCR_KEEP_SAMPLES` включается сразу, места это занимает копейки.

#### Планируемый пайплайн (M13)

```
attachment (bytes)
  └─ preprocess.py   upscale ×2 → grayscale → CLAHE → денойз → адаптивная бинаризация
                     → опциональный кроп по региону инвентаря
  └─ engine          tesseract(rus+eng) | paddleocr | Google Vision   → tuple[OcrLine, ...]
  └─ matcher.py      normalize → RapidFuzz token_set_ratio ≥ 85
                     → приоритет категории по типу тикета (boost/resource)
                     → извлечение количества (×3, x3, "3 шт")
  └─ pricing         quantity × price из item database → total_estimate
  └─ OcrResult       + confidence + warnings
```

#### Как это выглядит в карточке тикета

```
🔍 Распознано автоматически
 🚀 Топот        × 3   →   930 000 ₽
 ⚡ Ускорение    × 1   →   150 000 ₽
 ❓ «Хвocт тушкaна»    →   не найден в базе (совпадение 71 %)
━━━━━━━━━━━━━━━━━━━━━
💰 Предварительно: 1 080 000 ₽     🎯 Уверенность: 92 %
⚠️ Проверьте перед подтверждением — распознавание может ошибаться
```

Модал `💰 Сумма сделки` открывается с предзаполненным `1080000`; админ правит или принимает.

#### Правила, которые соблюдаются с самого начала

- OCR выполняется **в фоне** (`asyncio.create_task`), карточка обновляется по готовности.
  Медленный или упавший OCR **не блокирует** оформление сделки.
- `ocr_status=failed` → карточка выглядит ровно как в v1.0, админ вводит сумму руками.
- Тяжёлые зависимости (`opencv`, `paddleocr`) — опциональный extra `.[ocr]`;
  без них бот запускается и работает с `NullOcrGateway`.
- Распознавание идёт по байтам, уже скачанным для перезаливки в лог-канал (§11.5),
  — дополнительных загрузок нет.

---

## 12. Логирование, ошибки, наблюдаемость

- **Trace ID** (`uuid4().hex[:8]`) генерируется на каждое взаимодействие, прокидывается через
  `contextvars` во все слои, попадает в логи и в embed ошибки — пользователь называет ID,
  админ мгновенно находит контекст.
- Иерархия исключений:

```
StalbotError
├─ DomainError
│   ├─ AmountParseError
│   ├─ NickNotBoundError
│   ├─ ItemNotFoundError
│   ├─ DuplicateItemError
│   └─ InvalidPeriodError
├─ InfrastructureError
│   ├─ SheetsUnavailableError
│   ├─ SheetsWriteConflictError
│   └─ CacheStaleError
└─ PermissionError
```

- Глобальный `on_app_command_error` маппит исключение → понятный embed + аудит-запись.
  Неизвестные исключения → «Внутренняя ошибка, trace: `a1b2c3d4`» + полный traceback в файл.
- Метрики в лог раз в минуту: запросов к Sheets, хитрейт кэша, длительность синка,
  размер очереди аудита.
- `/healthcheck` (скрытая админская) — состояние Sheets, кэша, задержки синка, uptime.

---

## 13. Тестирование и качество

| Уровень | Что покрывается |
|---------|-----------------|
| Unit | `money.py` (≥ 60 кейсов + hypothesis), `nick.py`, `clock.py`/`DateRange`, `RankLadder`, `ReferralLadder`, парсер TXT прайса, маппинг `SYNC_LAYOUTS`, A1-нотация, генерация формул |
| Unit (сервисы) | `TransactionService`, `StatsService`, `PricingService`, `ProgressionService` на фейковых портах |
| Integration | SQLite-репозитории на реальной in-memory БД; `SheetsClient` против записанных фикстур ответов API |
| Contract | `test_ladder_matches_sheet_formula` — пороги в коде == пороги в формулах таблицы |
| Smoke | старт бота с фейковым Discord-клиентом, регистрация всех команд без ошибок |

CI (GitHub Actions): `ruff check` → `ruff format --check` → `mypy --strict` → `pytest --cov`
(порог покрытия 85 %). Pre-commit hooks локально.

---

## 14. Деплой и эксплуатация

- `.env` (валидируется `pydantic-settings`, бот падает на старте при отсутствии обязательных):

```
# --- Discord ---
DISCORD_TOKEN=                       # ⚠️ перевыпустить в Developer Portal, в чат не присылать
GUILD_ID=1475147129201627208
LOG_CHANNEL_ID=1518330495505797143
REVIEWS_CHANNEL_ID=1490342809075716237
# Отдельных каналов анонсов и архива скриншотов НЕТ:
# поздравления идут в канал события, скриншоты — в лог-канал (§9.2, §11.5)

# --- Google Sheets ---
GOOGLE_CREDENTIALS_PATH=./credentials/service_account.json
SPREADSHEET_ID=1W3HDdzvnQ4Uzyn86RQUUp-hrzFgBikowtP5LBoq_Ov0

# --- Кэш и фоновые задачи ---
CACHE_DB_PATH=./data/cache.sqlite3
SYNC_USERS_INTERVAL_SECONDS=180
SYNC_ITEMS_INTERVAL_SECONDS=600
PROGRESSION_POLL_SECONDS=300

# --- Поведение (§17.2) ---
PRICE_IMPORT_CONFIRM=true
ADMIN_CAN_VIEW_ANY_PROFILE=true

# --- OCR (задел под M13; в v1.0 работает NullOcrGateway) ---
OCR_ENABLED=false
OCR_ENGINE=null                      # null | tesseract | paddle | vision
OCR_KEEP_SAMPLES=true                # ★ включить СРАЗУ — копим датасет с первого дня
OCR_SAMPLES_DIR=./data/ocr_samples
OCR_MATCH_THRESHOLD=85
OCR_TIMEOUT_SECONDS=20

LOG_LEVEL=INFO
```

> Реквизиты проекта и их статус — в §17.3. Токен бота требует перевыпуска (§17.4).

- Dockerfile (python:3.12-slim, non-root, volume под `data/` и `credentials/`) + `docker compose`.
- `systemd`-юнит как альтернатива для VPS без Docker.
- Резервное копирование: ежедневный дамп `cache.sqlite3` и снапшот item database в TXT.
- Graceful shutdown: дослать очередь аудита, закрыть SQLite, закрыть сессию Discord.

---

## 15. Этапы работ (Milestones)

> Оценки — в человеко-днях при полноценной работе одного разработчика.

### M0 — Каркас проекта (0.5 д)
- `pyproject.toml`, `ruff`/`mypy`/`pytest` конфиги, pre-commit, `.gitignore`, `.env.example`.
- Структура пакетов, `Settings`, `__main__.py`, запуск пустого бота.
- **DoD:** `python -m stalbot` подключается к Discord; `ruff` и `mypy --strict` чисты.

### M1 — Core: деньги, время, embed'ы, аудит (1.5 д)
- `domain/money.py` (парсер + калькулятор + форматтер) с полным набором тестов.
- `domain/clock.py`, `domain/nick.py`, `domain/enums.py`, `domain/errors.py`.
- `EmbedFactory`, палитра, прогресс-бары, словарь эмодзи.
- `AuditGateway` + очередь + глобальный обработчик ошибок + `@admin_only`.
- **DoD:** тестовая команда `/ping` пишет корректный лог в канал `1518330495505797143`;
  `parse_amount("299 900 ₽ + 10000") == 309900`.

### M2 — Sheets + кэш (2 д)
- `SheetsClient`, rate limiter, retry, `a1.py`, `layouts.py`, `protection.py` (защита формул).
- SQLite: схема, миграции, репозитории, `sync.py`, фоновые задачи.
- Полная синхронизация при старте, метрики синка.
- **DoD:** после старта в SQLite лежат все предметы, пользователи и сделки;
  повторный синк укладывается в ≤ 2 запроса к API.

### M3 — Домен прогрессии + роли (1 д)
- `RankLadder`, `ReferralLadder`, описания бонусов, `progression_state`.
- `RoleGateway`, `ProgressionService.sync()`, поллер, детект буста сервера.
- Публичные поздравления.
- **DoD:** ручное изменение XP в таблице → в течение 5 мин выдана роль и отправлено поздравление,
  повторно не дублируется.

### M4 — `/add` (1 д)
- `TransactionService.register()` (общий для команды и тикетов), идемпотентность, read-back.
- Эфемерная карточка + публичное сообщение с напоминанием об отзыве.
- **DoD:** сделка появляется в таблице с корректными формулами `F`/`G`; кэш и роли обновлены.

### M5 — `/profile`, `/referrals` (1 д)
- Проверка привязки Discord ↔ ник, эмбеды, прогресс-бары, пагинация списка рефералов.
- **DoD:** чужой ник даёт отказ; свой — полный корректный профиль.

### M6 — База предметов и цены (2.5 д)
- `/price_list`, `/give_price`, `/new_price`, `/setprice`, `/setboost`,
  `/sync_prices`, `/item_add`, `/del_item`.
- Общий рендерер `PriceChangeReport` для трёх команд.
- Резолвер эмодзи, autocomplete.
- **DoD:** цикл `give_price → правка файла → new_price → sync_prices` меняет цены
  на всех трёх листах и в item database; `/del_item` не оставляет дыр.

### M7 — Статистика (1.5 д)
- `StatsService`, `/logs` с пагинацией и посуточной нумерацией, `/day`, `/week`, `/month`.
- **DoD:** суммы совпадают с ручным расчётом по таблице; пагинация переживает 500+ сделок.

### M8 — Ручные выдачи и `/tag` (0.5 д)
- `/set_referral`, `/set_rank`, `/tag` с DM и фолбэком.
- **DoD:** роли выданы, публичные сообщения оформлены, фоновый поллер их не снимает.

### M9 — Тикеты: продажа предметов и бустов (2 д)
- Слушатель категорий, ожидание Ticket Tool, панель, persistent views.
- Выбор способа, модалы, карточка, скриншот, админское подтверждение.
- Перезаливка скриншота в лог-канал через `attachment://` (постоянные ссылки).
- **DoD:** полный путь от создания канала до строки в таблице; после рестарта бота
  кнопки на старых карточках работают.

### M10 — Тикеты: заказ бустов (2 д)
- Редактор заказа in-place, select-меню, `+`/`−`/ввод количества, добавление/удаление,
  расчёт сумм, подтверждение.
- **DoD:** заказ из 5 позиций редактируется без появления лишних сообщений в канале;
  черновик переживает рестарт.

### M11 — Полировка и наблюдаемость (1 д)
- Единая вычитка всех текстов и эмодзи, проверка лимитов embed'ов.
- `/healthcheck`, метрики, graceful shutdown.
- В `/healthcheck` — счётчик накопленного датасета OCR (готовность к M13).
- **DoD:** визуальное ревью всех 19 команд + 3 типов тикетов.

### M12 — Документация, тесты, деплой (1 д)
- README (установка, права service account, настройка Discord-приложения), docstrings-ревизия.
- Docker/systemd, бэкапы, CI.

**Итого v1.0: ~17.5 человеко-дней.**

Критический путь: `M0 → M1 → M2 → M4 → M9 → M10`.
M5, M6, M7, M8 могут выполняться параллельно после M2.

---

### M13 — OCR скриншотов (2.5–3 д) · **после v1.0, не раньше чем через 2–4 недели работы**

Отдельный этап, запускается **только когда накоплен датасет** (см. §11.8).
Вся обвязка уже стоит в v1.0, поэтому M13 не трогает флоу тикетов —
меняется одна строка в `bootstrap.py` и появляются реализации порта.

**Предусловие входа:** в `data/ocr_samples/` ≥ 150 реальных скриншотов, из них
≥ 50 с подтверждённой админом суммой (пара «картинка → правильный ответ»).
Пока условие не выполнено — этап не начинается, иначе движок настраивается вслепую.

- [ ] Разметочный скрипт `tools/label_samples.py` — прогнать образцы, свести
      «скриншот ↔ сумма, которую реально ввёл админ» в CSV-эталон.
- [ ] `infrastructure/ocr/preprocess.py` — upscale ×2, grayscale, CLAHE, денойз,
      адаптивная бинаризация, опциональный кроп ROI.
- [ ] Бенчмарк движков **на своём датасете**, а не по обзорам:
      `tesseract(rus+eng)` vs `paddleocr` vs Google Cloud Vision.
      Метрики: точность суммы (±0 ₽), полнота по предметам, latency, стоимость вызова.
- [ ] `infrastructure/ocr/matcher.py` — RapidFuzz `token_set_ratio ≥ OCR_MATCH_THRESHOLD`,
      приоритет категории по типу тикета, извлечение количества (`×3`, `x3`, `3 шт`).
- [ ] Числа из распознанного текста — **через тот же `domain/money.py`** (§5.1),
      никакого второго парсера.
- [ ] Расчёт `total_estimate` по item database + `confidence` + `warnings`.
- [ ] Блок «🔍 Распознано автоматически» в карточке тикета, предзаполнение модала суммы.
- [ ] Фоновое выполнение, таймаут `OCR_TIMEOUT_SECONDS`, деградация в `ocr_status=failed`.
- [ ] Опциональный extra `pip install -e .[ocr]`; без него бот работает на `NullOcrGateway`.
- [ ] Регрессионный тест на «золотой» выборке: точность суммы не ниже зафиксированного порога.

**DoD:** на отложенной выборке из 30 скриншотов, не участвовавших в настройке,
сумма распознаётся верно в ≥ 80 % случаев; при выключенном `OCR_ENABLED` поведение
бота **побайтово** совпадает с v1.0.

**Итого с OCR: ~20.5 человеко-дней.**

> **Бэклог после M13:** промокоды Барона, ежемесячный доход Legend, `/calc`,
> экспорт статистики в CSV, автоприменение скидок ранга к заказу бустов.

---

## 16. Риски

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Лимиты Google Sheets API (60 req/min) | Средняя | Высокое | SQLite-кэш, батчинг, token bucket, backoff |
| Случайная перезапись формулы ботом | Низкая | **Критическое** | `READ_ONLY_RANGES` + `ProtectedRangeWriteError` до вызова API, `valueInputOption=RAW` везде, юнит-тест на все места записи (§7.3) |
| Формулы протянуты не до конца / диапазоны кончились | Высокая (со временем) | Высокое | Счётчик свободных строк при каждом синке, предупреждение в лог-канал при остатке < 50, резервный `copyPaste(PASTE_FORMULA)` |
| Истечение CDN-ссылок на скриншоты | Высокая | Среднее | Перезаливка байтов в лог-канал через `attachment://` — ссылка живёт вместе с сообщением (§11.5) |
| Нестабильные `id` после `/del_item` (перенумерация) | Средняя | Среднее | В черновиках заказов хранится `name_norm` + `category`, переразрешение позиции по имени |
| Лимит 25 опций в select при большом числе бустов | Средняя | Среднее | `PaginatedItemSelect` с сохранением выбора между страницами |
| Гонки при параллельных `/add` | Низкая | Высокое | Блокировка записи + read-back + идемпотентность |
| Задержка пересчёта формул после записи | Средняя | Среднее | Read-back с retry перед показом начисленных 🪙/⚡ |
| Расхождение порогов в коде и в формулах | Средняя | Среднее | Контрактный тест в CI |
| Ручные правки таблицы ломают структуру | Средняя | Высокое | Валидация заголовков при синке, отказ работать при несовпадении |
| **M13:** датасет не накоплен к началу OCR | Средняя | Высокое | `OCR_KEEP_SAMPLES=true` включён с M9; вход в M13 заблокирован до ≥ 150 образцов (§15) |
| **M13:** ни один движок не даёт приемлемой точности на шрифте Stalcraft | Средняя | Среднее | Бенчмарк трёх движков на своём датасете до написания интеграции; фолбэк — ручной ввод, он и так остаётся основным |
| **M13:** тяжёлые зависимости (`opencv`, `paddleocr`) ломают деплой v1.0 | Низкая | Среднее | Опциональный extra `.[ocr]`, `NullOcrGateway` по умолчанию, бот стартует без них |
| Утечка секретов (токен, ключ service account) | **Реализовалась** | **Критическое** | Токен перевыпустить; `.gitignore` покрывает `credentials/`, `.env`, `*service_account*.json`; в git ничего не попало (проверено) |

---

## 17. Принятые решения и оставшиеся мелочи

### 17.1 ✅ Решения заказчика (закрыто)

| # | Вопрос | Решение | Где реализовано |
|---|--------|---------|-----------------|
| 1 | Расхождения «текст описания ↔ формулы» (10 шт.) | **Истина — формулы.** Тексты бонусов в интерфейсе приведены к формулам | §9.1.1 |
| 2 | Правка формул, `ARRAYFORMULA` | **Не трогать.** Бот формулы не пишет вообще | §7.3 |
| 3 | Лимит диапазонов `845` | Заказчик продлевает вручную; бот предупреждает при остатке < 50 строк | §7.3, §9.1.1 |
| 4 | Зелёная кнопка «Перейти к тикету» | Принята серая link-кнопка (ограничение Discord) | §10.13 |
| 5 | Календарь в модале | Текстовое поле + устойчивый парсер (`31.07.2026 21:00`, `завтра 20:00`, …) | §11.4 |
| 6 | User-picker в модале тикета | Текстовое поле + автосопоставление с участниками сервера | §11.4 |
| 7 | Лимит 25 опций в select | Постраничный `PaginatedItemSelect` | §11.6 |
| 8 | Редактор заказа бустов | Одно сообщение, редактируемое in-place | §11.6 |
| 9 | `/set_rank` | Выдаёт **только Discord-роль**, таблица не меняется | §10.12 |
| 10 | `/set_referral` | Пишет реферала **только в первую строку** игрока | §10.12 |
| 11 | «Чистая прибыль» | `Σ покупок (у меня) − Σ продаж (мне)` | §10.11 |
| 12 | `/del_item` и ID | **Перенумеровывать**; ID нестабилен, ссылки хранятся по имени | §10.9 |
| 13 | Канал поздравлений о повышении | Отдельного канала нет: сообщение идёт в канал события (тикет / канал команды), у фонового поллера — в лог-канал | §9.2 |
| 14 | Роль 🤝 Партнёр | `1518584570457358556` | §9.1 |
| 15 | Архив скриншотов | Отдельного канала нет: копия карточки со скриншотом внутри embed уходит в лог-канал через `attachment://` | §11.5 |

### 17.2 Решения по умолчанию (можно поменять одной строкой в конфиге)

Не блокируют работу — реализую так, если не скажете иначе.

1. **`/new_price` — шаг подтверждения включён**: сначала diff, потом кнопка `✅ Применить`.
   Флаг `PRICE_IMPORT_CONFIRM=false` применяет сразу.
2. **`/profile` и `/referrals` для админов** — админ может смотреть чужие профили.
   Флаг `ADMIN_CAN_VIEW_ANY_PROFILE=false` запрещает.
3. **Привязка ник ↔ Discord — строго один к одному.** Попытка привязать ник ко второму
   аккаунту (или аккаунт ко второму нику) даёт предупреждение админу с кнопкой
   подтверждения перепривязки.
4. **Отзыв после сделки** — только напоминание со ссылкой на канал `1490342809075716237`,
   факт оставления отзыва бот не отслеживает.
5. **Язык кода** — идентификаторы, docstrings и технические логи на английском,
   весь пользовательский текст (embed'ы, команды, ошибки) на русском.
6. **`/set_rank` — toggle**: повторный вызов с тем же рангом снимает ручную роль.

### 17.3 Реквизиты проекта

| Параметр | Значение | Статус |
|----------|----------|--------|
| `GUILD_ID` | `1475147129201627208` | ✅ получено |
| `SPREADSHEET_ID` | `1W3HDdzvnQ4Uzyn86RQUUp-hrzFgBikowtP5LBoq_Ov0` | ✅ получено |
| Ключ service account | `credentials/service_account.json` (тип `service_account`, проект `test-ds-bot`) | ✅ на месте |
| E-mail service account | `discord-bot-sa@test-ds-bot.iam.gserviceaccount.com` | ✅ получено |
| `DISCORD_TOKEN` | — | ⛔ см. ниже |

Значения выше нужно продублировать в `.env` при старте M0. **Токен в репозиторий,
в план и в любой файл под контролем версий не записывается ни при каких условиях.**

### 17.4 ⛔ Открыто: безопасность

- [ ] **Перевыпустить `DISCORD_TOKEN`.** Прежний токен был передан открытым текстом
      в переписке и считается скомпрометированным.
      Developer Portal → приложение → **Bot** → **Reset Token**.
      Новый вставить в `.env` вручную; в чат его отправлять не нужно.
- [ ] **Выдать доступ «Редактор»** к таблице на
      `discord-bot-sa@test-ds-bot.iam.gserviceaccount.com` — без этого бот таблицу не увидит.
- [x] `.gitignore` покрывает `.env`, `credentials/`, `*service_account*.json`, `data/`.
      Проверено: секретов в индексе git нет.

### 17.5 Что нужно от вас перед стартом M0

- [ ] Подтвердить, что формулы `F`/`G` протянуты вниз с запасом и закреплённые
      диапазоны (`845`) продлены — бот на старте это проверит и напишет фактические цифры.
- [ ] Права бота на сервере: `Manage Roles` (и его роль **выше** всех выдаваемых ролей),
      `View Channels`, `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`
      в категориях тикетов и лог-канале. Intents: `guilds`, `members`, `message_content`,
      `guild_messages`.
- [ ] Сверить точные названия листов: `DataBase`, `Мейн Скуп`, `Скуп бустов`, `БУСТЫ`
      (регистр и пробелы важны — бот сверит их при первом запуске и сообщит о расхождении).

---

*Документ актуален на 31.07.2026. Все продуктовые решения приняты, реализация не заблокирована;
единственное открытое действие — перевыпуск токена (§17.4).
Прогресс выполнения — в `PLAN_PROGRESS.md`.*
