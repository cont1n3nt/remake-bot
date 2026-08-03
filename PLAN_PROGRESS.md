# PLAN_PROGRESS.md — трекер выполнения

> Отражает фактическое состояние проекта. Полное описание задач — в [`PLAN.md`](PLAN.md).
>
> **Обозначения:** `[ ]` не начато · `[~]` в работе · `[x]` готово и проверено · `[!]` заблокировано
>
> **Последнее обновление:** 03.08.2026
> **Готовность v1.0:** ▰▰▰▰▰▰▰▰▰▰ **100 %** (13 / 13 этапов, M0–M12)
> **Готовность с OCR:** ▰▰▰▰▰▰▰▰▰▱ **93 %** (13 / 14 этапов, M0–M13)
> **Продуктовых блокеров нет** — все решения приняты (§17.1 в `PLAN.md`).
> **⛔ Открыто одно действие:** перевыпустить `DISCORD_TOKEN` (§17.4).

---

## Сводка по этапам

| Этап | Название | Статус | Готовность | Оценка |
|------|----------|--------|------------|--------|
| M0 | Каркас проекта | `[x]` | 100 % | 0.5 д |
| M1 | Core: деньги, время, embed'ы, аудит | `[x]` | 100 % | 1.5 д |
| M2 | Google Sheets + SQLite-кэш | `[x]` | 100 % | 2 д |
| M3 | Домен прогрессии + Discord-роли | `[x]` | 100 % | 1 д |
| M4 | `/add` | `[x]` | 100 % | 1 д |
| M5 | `/profile`, `/referrals` | `[x]` | 100 % | 1 д |
| M6 | База предметов и цены | `[x]` | 100 % | 2.5 д |
| M7 | Статистика | `[x]` | 100 % | 1.5 д |
| M8 | Ручные выдачи и `/tag` | `[x]` | 100 % | 0.5 д |
| M9 | Тикеты: продажа предметов и бустов | `[x]` | 100 % | 2 д |
| M10 | Тикеты: заказ бустов | `[x]` | 100 % | 2 д |
| M11 | Полировка и наблюдаемость | `[x]` | 100 % | 1 д |
| M12 | Документация, тесты, деплой | `[x]` | 100 % | 1 д |
| — | — | — | — | — |
| **M13** | **OCR скриншотов** *(после v1.0)* | `[ ]` | 0 % | 2.5–3 д |

**Итого v1.0: ~17.5 человеко-дней. С OCR: ~20.5.**
Критический путь: `M0 → M1 → M2 → M4 → M9 → M10`. M5–M8 параллелятся после M2.

⚠️ **M13 не начинается, пока не накоплен датасет** — минимум 150 реальных скриншотов
из тикетов, из них ≥ 50 с подтверждённой суммой. Копятся автоматически с M9
(`OCR_KEEP_SAMPLES=true`), то есть 2–4 недели живой работы бота.

---

## ⛳ Чек-лист «перед стартом»

### Получено ✅

- [x] `GUILD_ID` = `1475147129201627208`
- [x] `SPREADSHEET_ID` = `1W3HDdzvnQ4Uzyn86RQUUp-hrzFgBikowtP5LBoq_Ov0`
- [x] Ключ service account → `credentials/service_account.json`
      (тип `service_account`, проект `test-ds-bot`) — лежит правильно, перемещать не нужно
- [x] `.gitignore` покрывает `.env`, `credentials/`, `*service_account*.json`, `data/`;
      проверено — секретов в индексе git нет

### ⛔ Требует действий заказчика

- [ ] **Перевыпустить `DISCORD_TOKEN`** — прежний передан открытым текстом и скомпрометирован.
      Developer Portal → приложение → Bot → Reset Token → вставить в `.env` вручную
- [ ] **Выдать доступ «Редактор»** к таблице на
      `discord-bot-sa@test-ds-bot.iam.gserviceaccount.com`
- [ ] Формулы `F`/`G` протянуты вниз с запасом, диапазоны `845` продлены
- [ ] Права бота: `Manage Roles` (роль бота **выше** выдаваемых), `View Channels`,
      `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`
- [ ] Intents: `guilds`, `members`, `message_content`, `guild_messages`
- [ ] Сверить точные названия листов: `DataBase`, `Мейн Скуп`, `Скуп бустов`, `БУСТЫ`

---

## 🔒 Инварианты проекта (проверять на каждом этапе)

- [ ] Бот **никогда** не пишет в формульные колонки `F, G, J, K, L, M, N, O, P, R, S`
- [ ] `valueInputOption=RAW` во **всех** записях, `USER_ENTERED` не используется нигде
- [ ] Все денежные значения проходят через `domain/money.py` — ни одного `int(...)`/`float(...)` над пользовательским вводом
- [ ] Все даты — `GMT+3`, ни одного naive `datetime.now()`
- [ ] Все embed'ы — только через `EmbedFactory`, ни одного прямого `discord.Embed(...)` в cog'ах
- [ ] Пороги рангов/реф-ролей в коде == пороги в формулах (контрактный тест)
- [ ] Тикеты вызывают `OcrGateway` **всегда** (в v1.0 — `NullOcrGateway`); прямых веток
      «если OCR выключен» во флоу нет
- [ ] Карточка тикета строится функцией `render_ticket_card(session)` из состояния,
      а не собирается по месту — иначе OCR-блок в M13 придётся вшивать в каждый обработчик
- [ ] Модал суммы принимает `default` (в v1.0 всегда `None`)

---

## M0 — Каркас проекта · `[x]` 100 %

- [x] `pyproject.toml`: зависимости, `ruff`, `mypy --strict`, `pytest`, `coverage`
- [x] `.gitignore` (`credentials/`, `data/`, `.env`), `.env.example`
- [x] Дерево пакетов `src/stalbot/{domain,application,infrastructure,presentation,config}`
- [x] `config/settings.py` на `pydantic-settings` + fail-fast валидация
- [x] `config/ids.py` — каналы, категории тикетов, ID ролей рангов и реф-ролей, `PARTNER_ROLE_ID`, Ticket Tool
- [x] `presentation/bot.py` + `__main__.py`, запуск пустого бота
- [x] `bootstrap.py` — заготовка композиционного корня
- [x] `pre-commit` хуки

**DoD:** `ruff check`, `ruff format --check` и `mypy --strict` — чисто (проверено). `python -m stalbot`
с плейсхолдер-токеном доходит до Discord API и падает на `401 Unauthorized` — это граница,
дальше которой нельзя проверить без реального `DISCORD_TOKEN` (см. ⛔ §17.4, токен ещё не
перевыпущен заказчиком). Полная проверка живого подключения — после перевыпуска токена.

---

## M1 — Core: деньги, время, embed'ы, аудит · `[x]` 100 %

### ★ `domain/money.py`
- [x] `parse_amount()` — нормализация Unicode, срезание валют, склейка разрядов, множители `к/кк/ккк/k/m/b`
- [x] `evaluate_amount()` — лексер чисел + AST-парсер с белым списком узлов (без `eval`)
- [x] `format_amount()` / `format_compact()`
- [x] Защита: лимит длины строки, глубины AST, показателя степени
- [x] Тесты: ≥ 60 параметризованных кейсов (113 тестов в `test_money.py`)
- [x] Тест-свойство `hypothesis`: `parse_amount(format_amount(x)) == x`

### Прочий core
- [x] `domain/clock.py` — `GMT3`, `SystemClock`, `DateRange`, парсеры дат
- [x] `domain/clock.py::parse_deadline()` — `31.07.2026 21:00`, `31.07 21:00`, `завтра 20:00`, `через 3 часа`
- [x] `domain/nick.py` — `normalize_nick()`
- [x] `domain/enums.py`, `domain/errors.py` (иерархия исключений)
- [x] `presentation/embeds/palette.py` — цвета, футер, единый эмодзи-словарь
- [x] `presentation/embeds/factory.py` — `success/info/warning/error/ticket/audit`
- [x] `presentation/embeds/progress.py` — прогресс-бары
- [x] Автообрезка под лимиты Discord (256 / 1024 / 4096 / 6000 / 25 полей) — `EmbedFactory` + публичная `enforce_limits()` для полей, добавленных после построения
- [x] `application/services/audit.py` + очередь + воркер + батчинг (до 10 embed'ов/сообщение)
- [x] `infrastructure/discord/audit_channel.py` → канал `1518330495505797143`
- [x] `presentation/checks.py` — `@admin_only()`
- [x] `presentation/errors.py` — глобальный `on_app_command_error`
- [x] `contextvars`-трассировка (`trace_id`) — `infrastructure/logging/trace.py`
- [x] `infrastructure/logging/setup.py` — `structlog`, JSON, ротация

**DoD:** тестовая `/ping` зарегистрирована и пишет embed в лог-канал через `AuditService` (проверено на уровне unit/wiring-тестов и запуском бота до границы `401 Unauthorized` — токен ещё не перевыпущен, см. ⛔ §17.4; живая проверка в Discord — после перевыпуска токена);
`parse_amount("299 900 ₽ + 10000")` — не относится к `parse_amount` (это `evaluate_amount`) — проверено: `evaluate_amount("299 900 ₽ + 10000") == Decimal("309900")`.

**Проверено:** `ruff check`, `ruff format --check`, `mypy --strict` — чисто на 48 файлах; `pytest` — 221 тест пройден, покрытие 91.53 % (порог 85 % пройден; домен ≥ 96 %, сервисы ≥ 98 %). Инварианты (нет naive `datetime.now()`, нет прямых `discord.Embed()` вне `factory.py`) проверены вручную.

---

## M2 — Google Sheets + SQLite-кэш · `[x]` 100 %

### Sheets
- [x] `infrastructure/sheets/a1.py` — колонки ↔ индексы, диапазоны
- [x] `infrastructure/sheets/client.py` — `batch_get` / `batch_update`, `to_thread`
- [x] `infrastructure/sheets/ratelimit.py` — token bucket 60/мин, backoff+jitter, блокировки записи
- [x] ★ `infrastructure/sheets/protection.py` — `READ_ONLY_RANGES` + `ProtectedRangeWriteError`
- [x] ★ Юнит-тест: перебрать все места записи в проекте, убедиться что формульные колонки не задеты
      (исчерпывающий перебор колонок `A…S`, `AA…AG` + AST-скан на посторонние вызовы `batch_update`/`write_verified`)
- [x] ★ `valueInputOption=RAW` жёстко зашит, `USER_ENTERED` отсутствует в коде (проверка тестом)
- [x] `infrastructure/sheets/layouts.py` — `SYNC_LAYOUTS`, карта блоков листа `DataBase`
- [x] Проверка «докуда протянуты формулы `F`/`G`» + предупреждение в лог при остатке < 50 строк
- [x] Резервный `copyPaste(pasteType=PASTE_FORMULA)` для строк без формул
- [x] Валидация структуры листа при старте (заголовки на месте)
- [x] Read-back верификация записи + компенсация при расхождении
- [x] Read-back с retry для `F`/`G` (ожидание пересчёта формул)

### Кэш
- [x] `infrastructure/cache/schema.sql` + миграции + версия схемы
- [x] ★ Таблица `screenshot_analyses` + колонки `ocr_status` / `ocr_analysis_id`
      в `ticket_sessions` — **создаются сразу**, чтобы M13 не требовал миграции
- [x] Репозитории: `items`, `users`, `transactions`, `progression_state`, `ticket_sessions`, `boost_order_lines`
- [x] Хранение денег как `TEXT` → `Decimal`
- [x] `infrastructure/cache/sync.py` — полный и инкрементальный синк
      (`sync_items` — 1 запрос/цикл каждые `SYNC_ITEMS_INTERVAL_SECONDS`; `sync_users_and_transactions` —
      2 запроса/цикл каждые `SYNC_USERS_INTERVAL_SECONDS`, включая проверку формул)
- [x] Фоновые задачи (`tasks.loop`) с настраиваемыми интервалами — в `presentation/bot.py::_setup_cache`
- [x] Inline-рефреш при устаревших данных (`STALE_AFTER`) — `CacheSync.ensure_fresh(max_age_seconds=...)`
- [x] Обязательный полный синк при старте до регистрации команд — `setup_hook` → `run_startup_sync()` → `tree.sync()`
- [x] Метрики синка в лог — `SyncReport` + `logger.info` на каждый цикл

**DoD:** проверено **вживую** против реальной таблицы (только операции чтения — `credentials/service_account.json`
уже даёт этого достаточно; доступ «Редактор» для записи по-прежнему не подтверждён, см. ⛔ §17.4):
`validate_layout()` проходит без расхождений; `run_startup_sync()` кладёт в SQLite
219 предметов, 237 пользователей, 64 валидные сделки (554 исторические строки без даты корректно
пропущены и залогированы — колонка «Дата» реально пуста у старых записей); `sync_users_and_transactions`
укладывается в 2 запроса, `sync_items` — в 1; попытка записи в `F`/`G`/`J`…`P`/`R`/`S` падает с
`ProtectedRangeWriteError` до сети (протестировано exhaustively). Живая проверка также поймала реальную
проблему таблицы: формулы `F`/`G` уже не покрывают весь диапазон данных (0 свободных строк) — бот
корректно предупреждает, это открытое действие для заказчика (§17.5), не баг бота.

**Важные находки в процессе (задокументированы прямо в коде):**
- Реальное имя листа — `Мейн скуп` (строчная «с»), а не `Мейн Скуп`, как в тексте плана; `layouts.py`
  и все заголовки `DATABASE_BLOCKS` собраны по факту из живой таблицы, а не по глоссам из PLAN.md.
- `UserProfile`/`TransactionRecord` не хранят `nick_display` в кэш-таблице `users`/`transactions`
  по схеме §8.1 «как есть» — `nick_display` для транзакций восстанавливается `LEFT JOIN users`
  (репозиторий транзакций), а для `users` передаётся отдельным параметром `nick_displays`, который
  `CacheSync` строит из исходной (не приведённой к нижнему регистру) колонки `B`.
- **Найден и исправлен баг в `SheetsClient.batch_get`**: Google Sheets API возвращает `range` в
  ответе в нормализованном виде (`"DataBase!A3:H1598"` вместо запрошенного `"DataBase!A3:H"`), из-за
  чего сопоставление по эхо-строке всегда давало пустой результат для открытых диапазонов — синк
  тихо писал 0 записей. Пойман только живой проверкой (юнит-тесты с фейковым клиентом эту ошибку не
  ловили, так как фейк эхировал точную строку запроса). Исправлено на позиционное сопоставление
  (Google гарантирует порядок `valueRanges` == порядку `ranges` в запросе).
- **Найден и исправлен мёртвый код в `_parse_items`**: `_to_int()` всегда возвращает `int` (0 по
  умолчанию), поэтому проверка `item_id is None` никогда не срабатывала — строка с пустым `id`
  получала `id=0` вместо пропуска. Пойман тестом `test_parse_items_skips_row_missing_id`.

---

## M3 — Домен прогрессии + Discord-роли · `[x]` 100 %

- [x] `domain/progression/ranks.py` — `RANKS` с порогами **50 / 300 / 1200 / 3500 / 7000**
- [x] `domain/progression/referrals.py` — `REFERRAL_ROLES` с порогами **1 / 3 / 7 / 20 / 50**
- [x] `PARTNER_ROLE_ID = 1518584570457358556` (3-й этап реферальной системы) — уже был в `config/ids.py`
      с M0, переэкспортирован из `domain/progression/referrals.py`. ⚠️ Сама выдача роли по совокупному
      обороту рефералов (SUMIF по H/O) **не реализована** — это агрегация по всем рефералам конкретного
      игрока, не привязанная ни к одному чек-листу текущего этапа; отложено до конкретной задачи
      (вероятно, вместе с M5 `/referrals`, которому нужен тот же реверс-индекс «кто кого пригласил»).
- [x] `domain/progression/perks.py` — тексты бонусов **по канону §9.1.1** (1 500 000 ₽ за Coin, буст = ⚡ 30, и т. д.)
      — только формуло-подтверждённые числа (разовые бонусы, бонус за крупную сделку, буст, XP-порог 250).
      Текстовые «скидка/наценка» и «приоритет в очереди» из мокапа `/profile` (§10.2) не формуло-подтверждены
      ни для одного ранга кроме примера Elite — сознательно не выдуманы, оставлены на M5.
- [x] Ревизия: старые числа из текстового описания не встречаются нигде в UI (проверено grep'ом)
- [x] Контрактный тест: пороги в коде == пороги в формулах `R` / `S` — сверено с замороженным снимком
      реальных формул `DataBase!R3`/`DataBase!S3`, снятых вживую в этой сессии
- [x] `infrastructure/discord/role_gateway.py` — выдача/снятие, взаимоисключение внутри лестницы
      (через `RoleSet.universe`, единая логика для обеих лестниц одновременно)
- [x] `application/services/progression.py` — `sync(nicks, *, announce_to=None)`
- [x] Маршрутизация поздравлений: канал события → лог-канал для фонового поллера
      (через `AuditGateway.send_batch`, когда `announce_to=None`)
- [x] Флаг `manual_rank_role` — поллер не снимает роли, выданные через `/set_rank`. Добавлена колонка
      `progression_state.manual_rank_role` (миграция схемы, `SCHEMA_VERSION` 1→2 — реальных данных для
      бэкфилла ещё нет, проект не в проде). Сама команда `/set_rank`, выставляющая флаг, — задача M8;
      здесь готова инфраструктура + `ProgressionService.sync()` уже уважает флаг.
- [x] Защита от повторных поздравлений (запись состояния до отправки)
- [x] Фоновый поллер `tasks.loop(minutes=5)` по всей базе — `PROGRESSION_POLL_SECONDS=300` (уже был в `Settings` с M0)
- [x] `on_member_update` → детект буста сервера → запись флага в колонку `Q`
- [x] Публичное поздравление с текущими 🪙 / ⚡ и списком новых бонусов

**DoD:** покрыто юнит-тестами (479 тестов, 94.81 % покрытия; `ruff`/`mypy --strict` чисты): смена ранга
в кэше → роль выдана и снята предыдущая (лестница), поздравление отправлено ровно один раз, состояние
записано *до* отправки, повторный синк без изменений не дублирует. Живая проверка «5 минут» и реальной
выдачи роли в Discord невозможна до перевыпуска `DISCORD_TOKEN` (⛔ §17.4) — интервал `tasks.loop`
подтверждён равным `PROGRESSION_POLL_SECONDS` (300 с) через unit-тест на сам конструктор луп'а.

---

## M4 — `/add` · `[x]` 100 %

- [x] `application/services/transactions.py` — `register()` (общий для команды и тикетов)
- [x] Идемпотентность через ключ в SQLite — новая таблица `write_idempotency`
      (`SCHEMA_VERSION` 2→3), ключ = `str(interaction.id)`; повтор возвращает закэшированную запись
      без повторной записи в Sheets
- [x] Валидации: реферал ≠ игрок (блокирует), конфликт привязки Discord ↔ ник — один к одному
      (эфемерный `ConfirmView` с кнопками Подтвердить/Отмена, автор-заблокирован, таймаут 60с)
- [x] Запись строки `A, B, C, D, E, H` — **без `F`/`G`**. `H` (реферал) пишется **только на первой
      сделке игрока** — иначе `СЧЁТЕСЛИ($H:$H; ник)` в колонке `P` задвоил бы счётчик рефералов
      при каждом повторном `/add` с тем же рефералом (не описано явно в §10.1, но необходимо для
      целостности данных; `/set_referral`, M8, по той же причине пишет только в первую строку)
- [x] Привязка Discord ID в колонку `I` — только если ещё не привязан или подтверждена перепривязка
- [x] Ожидание пересчёта `F`/`G` перед показом начисленных 🪙 / ⚡ — `read_until` (3×0.7с), при
      неудаче резервный `copyPaste(PASTE_FORMULA)` от последней формульной строки и повторное ожидание;
      если формулы так и не посчитались — эфемерный embed сообщает «ожидает пересчёта», не выдумывает 0
- [x] Точечный рефреш кэша (`CacheSync.sync_users_and_transactions()`) +
      `ProgressionService.sync([ник, реферал], announce_to=interaction.channel)`
- [x] Эфемерный embed «Сделка зафиксирована»: тип, ник, Discord, сумма, 🪙/⚡, реферал, дата, строка
- [x] Публичное сообщение + напоминание об отзыве (канал `1490342809075716237`, уже в `Settings` с M0)
- [x] Аудит-событие — не потребовало нового кода: `on_app_command_completion` (M1) уже логирует
      каждую успешную команду в лог-канал автоматически

**DoD:** покрыто тестами (510 тестов, 94.71 % покрытия; `ruff`/`mypy --strict` чисты): запись строки,
read-back верификация, ожидание/резервное копирование формул, точечный рефреш и синк ролей, повтор с
тем же `idempotency_key` не создаёт вторую запись. Smoke-тест подтвердил, что `TransactionsCog`
регистрируется в `bot.add_cog()` и `/add` реально появляется в дереве команд (без реального
подключения к Discord — токен всё ещё не перевыпущен, ⛔ §17.4).

**Найдено и исправлено в процессе:** имена тестовых файлов `test_transactions.py` дважды
конфликтовали между разными директориями (`tests/unit/infrastructure/cache/repositories/`,
`tests/unit/application/services/`, `tests/unit/presentation/cogs/`) — в `tests/` не было
`__init__.py`, из-за чего pytest/mypy резолвили все тестовые файлы как единое плоское пространство
имён. Исправлено раз и навсегда: добавлены `__init__.py` во все директории `tests/`, а не точечным
переименованием (как было сделано в M2 для похожей, тогда ещё не вскрытой, причины).

---

## M5 — `/profile`, `/referrals` · `[x]` 100 %

- [x] `application/services/profile.py` — проверка привязки Discord ↔ ник (`ProfileService.get_profile`/`list_referrals`)
- [x] Флаг `ADMIN_CAN_VIEW_ANY_PROFILE` (по умолчанию `true`) — уже был в `Settings` с M0, теперь фактически используется
- [x] `/profile`: 🪙 Coins, ⚡ XP, Ранг, Реф-роль, Приглашено, бонусы ранга, прогресс до следующего
- [x] `/referrals`: реф-роль + бонусы, список приглашённых (`ник → @tag`), прогресс, награда следующей роли
- [x] Пагинация списка рефералов (> 15) — `presentation/views/paginated_embed.py::PaginatedEmbedView` (Prev/Next, 15/страницу)
- [x] Оба embed'а эфемерные

**DoD:** чужой ник → отказ (для не-админа, `ProfileAccessDeniedError`); свой → полный корректный профиль. Проверено 27 новыми юнит-тестами (сервис, кэш-репозиторий, cog, `PaginatedEmbedView`).

**Реализация:**
- `domain/errors.py` — новые `PlayerNotFoundError` (ника нет в базе) и `ProfileAccessDeniedError` (чужой профиль без прав); замаплены в `presentation/errors.py` на тексты из §10.2.
- `domain/progression/ladder.py` — публичный `Ladder.threshold_of(tier)`: понадобился для строки прогресса (`value / threshold`) в `/profile`/`/referrals`, чтобы не лезть в приватный `_threshold_of` фабрики лестниц из presentation-слоя.
- `infrastructure/cache/repositories/transactions.py` — новый `list_referral_targets(referrer)`: `SELECT DISTINCT nick_norm ... WHERE referrer_norm = ?`. Работает корректно с тем, что `referrer_norm` пишется только в первую сделку реферала (M4/§7.4), поэтому обычный `DISTINCT`-скан уже даёт каждого реферала ровно один раз — доп. агрегации не потребовалось (в отличие от того, что предполагалось на M3 для роли 🤝 Партнёр по совокупному обороту — это отдельная, всё ещё не реализованная задача).
- `application/dto/profile_view.py` — `ProfileView` (профиль + оригинальный ник для отображения, т.к. `UserProfile` его не хранит — см. находку M2) и `ReferredPlayer` (ник + Discord ID одного реферала).
- `application/services/profile.py` — `ProfileService`: единая проверка доступа (`is_admin and admin_can_view_any` ИЛИ `profile.discord_id == requester_id`) переиспользуется и в `get_profile`, и в `list_referrals` (последний вызывает первый).
- `presentation/views/paginated_embed.py` — `PaginatedEmbedView`: минимальный Prev/Next-пейджер над готовым списком embed'ов, по образцу `ConfirmView` (privacy-lock на автора, `on_timeout` гасит кнопки). Сознательно не единый переиспользуемый компонент с `⏮ ◀ n/m ▶ ⏭` и переходом по номеру страницы — это нужно только `/logs` (M7), делать сейчас было бы преждевременной абстракцией под ещё не написанный код.
- `presentation/cogs/profile.py` — `ProfileCog`: `/profile` и `/referrals`, единственные две команды без `@admin_only()` в проекте. Бонусы ранга/реф-роли и прогресс-бар берутся из `RankLadder`/`ReferralLadder` (M3) — presentation-слой обращается к domain-лестницам напрямую для чистого UI-рендеринга, это не нарушает Clean Architecture (зависимость направлена внутрь). Промо-текст «скидка/наценка/приоритет в очереди» из мокапа §10.2 сознательно не вставлен — M3 явно оставила эти числа неподтверждёнными формулами ни для одного ранга кроме иллюстративного примера Elite (декларация A6: только формуло-подтверждённые числа), не изменилось.
- `presentation/bot.py`/`bootstrap` — `ProfileService` собран в `_setup_cache`, `ProfileCog` зарегистрирован в `add_cog`.

**Найдено и исправлено в процессе:** не найдено — этап прошёл без сюрпризов относительно предыдущих находок M2–M4.

---

## M6 — База предметов и цены · `[x]` 100 %

- [x] `infrastructure/discord/emoji_resolver.py` — имя → `<:name:id>`, кэш + `on_guild_emojis_update`
- [x] `presentation/views/paginated_select.py` — ★ `PaginatedItemSelect` (переиспользуется в M10)
- [x] `presentation/autocomplete.py` — fuzzy-поиск по кэшу, значение = `item_id`
- [x] `application/services/catalog.py` — CRUD item database
- [x] `application/services/pricing.py` + общий рендерер `PriceChangeReport`
- [x] `/price_list` — 2 категории, кнопки-переключатели, внутренняя пагинация
- [x] `/give_price` — TXT в фиксированном формате, `BytesIO`, UTF-8 BOM
- [x] `/new_price` — парсер TXT, автодетект кодировки, валидация до применения
- [x] `/new_price` — diff + кнопка `✅ Применить` (флаг `PRICE_IMPORT_CONFIRM`)
- [x] `/new_price` — отчёт по 3 группам (ресурсы / бусты / скуп бустов)
- [x] `/setprice`, `/setboost` — autocomplete по категории, `evaluate_amount`
- [x] `/sync_prices` — batch-чтение всех `SYNC_LAYOUTS`, запись только изменённых ячеек
- [x] `/item_add` — `id = max+1`, проверка дубля, проверка существования эмодзи на сервере
- [x] `/del_item` — удаление, сдвиг блока `AA:AG` вверх, **перенумерация ID**, очистка хвоста, бэкап
- [x] `/del_item` — переразрешение позиций в активных черновиках заказов по `name_norm`
- [x] Тесты: парсер TXT, маппинг layouts, определение «скупа бустов» по коллизии имён

**DoD:** цикл `give_price → правка → new_price → sync_prices` обновляет 3 листа + item database
(проверено юнит-тестами на всех трёх сервисных методах); `/del_item` не оставляет пустых строк, ID
идут подряд, активные заказы не ломаются (переразрешение по `name_norm`/`category` покрыто тестами).

**Реализация:** обнаружено и подтверждено, что доменный/сервисный/инфраструктурный слой этапа
(`domain/errors.py` доп. не потребовалось; `application/dto/{delete_item_result,price_change,
price_import,sync_prices_report}.py`; `application/services/{catalog,pricing}.py`;
`infrastructure/discord/emoji_resolver.py`; `infrastructure/cache/repositories/items.py` —
`get_by_id`/`save_delete_backup`; `infrastructure/cache/repositories/boost_order_lines.py` —
`reassign_item_id`/`delete_by_name`; `infrastructure/cache/sync.py::parse_items_block` вынесен
в публичную функцию для переиспользования при перенумерации; `presentation/autocomplete.py`;
`presentation/views/paginated_select.py`) уже был полностью реализован и покрыт тестами в этой
рабочей копии на момент начала этапа — доведён до зелёного состояния (`ruff`/`ruff format`/
`mypy --strict` были чисты, тесты проходили) без переделок, только точечные фиксы: `Select.values`
в тесте `PaginatedItemSelect` — сеттер приватного `_values` вместо read-only property; переформатирование.
- **Реализовано в этой сессии — presentation-слой команд**, которого не хватало для завершения DoD:
  `presentation/cogs/catalog.py` — `CatalogCog`: `/item_add` (проверка дубля через
  `DuplicateItemError`, некритичное предупреждение о ненайденном эмодзи), `/del_item` (autocomplete
  по всему каталогу — значение `item_id`, как и было спроектировано в `autocomplete.py`'s docstring;
  предупреждение о затронутых черновиках заказов бустов), `/price_list` (`_PriceListView` — кнопки
  переключения категории + внутренняя пагинация 15/страницу, оба состояния синхронизированы в одном
  `_sync()`), `/give_price` (`discord.File` из `BytesIO`, UTF-8 BOM). `presentation/cogs/pricing.py` —
  `PricingCog`: `/setprice` (пишет `PriceField.BUY` ресурса), `/setboost` (пишет `PriceField.SELL`
  буста) — оба через общий `render_price_change_report` (добавлен в `application/services/pricing.py`,
  строит 3 блока через уже готовый `group_price_changes`); `/sync_prices` (отчёт `✅/⚠️/➖` с обрезкой
  списка ненайденных до 10 + счётчик остатка); `/new_price` (валидация расширения/размера/кодировки
  до парсинга, показ ошибок без применения при невалидном плане, `ConfirmView` при
  `PRICE_IMPORT_CONFIRM=true`, немедленное применение при `false`). `presentation/bot.py` —
  `EmojiResolver` подключён как `bot.emoji_resolver`, обновляется в `on_ready` (как только гильдия
  доступна) и на каждом `on_guild_emojis_update`; `CatalogService`/`PricingService` собраны в
  `_setup_cache`, оба cog'а зарегистрированы.
- **Осознанно не реализовано:** автоматический запуск `/sync_prices` сразу после `/new_price`
  (§10.6 п. 7 — прямо названо «опционально», не входит в чек-лист/DoD этапа; отдельный флаг конфига
  ради одной опциональной кнопки не заводился).
- **Осознанное отклонение от буквальной таблицы аргументов §10.9:** `/del_item` использует один
  аргумент `предмет` (`int`, autocomplete по всему каталогу, значение — `item_id`), а не
  `название` (str) + опциональный `категория`, показываемый «только при дубле» — Discord
  slash-command опции не могут быть условно видимыми в зависимости от значения другого аргумента, и
  `presentation/autocomplete.py::item_choices()` (уже существовавший на момент начала этапа) прямо
  документирует себя как общий для `/setprice`, `/setboost` **и** `/del_item` с id-значением
  «чтобы дубль имени между категориями не резолвился неоднозначно» — тот же способ уже применяется
  для `/setprice`/`/setboost`, так что `/del_item` просто следует уже принятому в этом же этапе решению.

Всего 616 тестов (было 587 до презентационного слоя), покрытие 94.93 % (`ruff`/`ruff format`/
`mypy --strict` чисты на 157 файлах). Инварианты (запись только в незащищённые колонки, `RAW`
везде, деньги только через `domain/money.py`, embed'ы только через `EmbedFactory`) проверены
вручную grep'ом по `presentation/cogs/` и `application/`.

---

## M7 — Статистика · `[x]` 100 %

- [x] `application/services/stats.py` — `report(period: DateRange) -> PeriodReport`
- [x] Посуточная нумерация сделок (`ROW_NUMBER() OVER (PARTITION BY date …)`), сброс в 00:00 GMT+3
- [x] `/logs` — пагинация `⏮ ◀ 3/17 ▶ ⏭` + переход к странице через модал, привязка View к автору
- [x] `/day` — таблица игроков + итоги
- [x] Чистая прибыль = `Σ покупок (у меня) − Σ продаж (мне)`, отрицательная помечается `🔻`
- [x] `/week` — валидация диапазона (конец ≥ начала, ≤ 31 дня, не в будущем)
- [x] `/month` — выбор месяца и года
- [x] Пагинация списка игроков при > 20
- [x] Тесты агрегаций на фикстурах

**DoD:** суммы совпадают с ручным расчётом (проверено юнит-тестами на фикстурах —
агрегация покупок/продаж по игроку, сортировка по обороту, чистая прибыль, включая
отрицательный случай); пагинация выдерживает 500+ сделок (`/logs` строит страницы по 25 через
оконную функцию `ROW_NUMBER()` в одном SQL-запросе на страницу, а не вычитывает всю таблицу
в память; `/day`/`/week`/`/month` пагинируют список игроков по 20 тем же `PaginatedEmbedView`,
что и `/referrals`).

**Реализация:**
- `domain/clock.py::parse_date()` — строгий парсер `ДД.ММ.ГГГГ` (без относительных фраз и
  двухзначного года, в отличие от `parse_deadline`; не использует `datetime.strptime()` —
  собственный разбор по `.`, чтобы не тянуть за собой naive `datetime` и лишний `DTZ`-риск).
  Ошибка формата/несуществующей даты — `InvalidPeriodError` (уже существовал в `domain/errors.py`,
  под именно такой класс ошибок он и заводился в M2).
- `application/dto/{period_report,log_entry}.py` — `PlayerPeriodStats`/`PeriodReport`
  (`turnover`/`total_purchases`/`total_sales`/`net_profit` — вычисляемые свойства, не хранимые
  поля, чтобы не рассинхронизировались) и `LogEntry` (нумерация внутри дня + `discord_id`,
  которого нет в самом `TransactionRecord` — эта сущность отражает только блок `Тикеты` листа,
  привязку к Discord `/logs` подтягивает отдельно).
- `infrastructure/cache/repositories/transactions.py` — `count_all()` и
  `list_numbered_page(offset, limit)`: `day_number` считается оконной функцией
  `ROW_NUMBER() OVER (PARTITION BY date(occurred_at) ORDER BY sheet_row)` **до** пагинации в
  одном CTE-запросе — номер сделки внутри дня не зависит от того, какая страница запрошена.
- `application/services/stats.py` — `StatsService.report(period)`: читает `list_by_period` за
  `[00:00:00, 23:59:59.999999]` GMT+3 запрошенного диапазона, агрегирует покупки/продажи по
  нормализованному нику, подтягивает `discord_id` через `UsersCacheRepository`, сортирует по
  обороту по убыванию.
- `presentation/views/logs_pager.py` — `LogsPagerView`: `⏮ ◀ n/m ▶ ⏭` + `🔢 К странице`
  (первый модал в проекте — `_JumpToPageModal`). Заведён отдельным компонентом, а не как
  надстройка над `PaginatedEmbedView` (`/referrals`, M5) — комментарий в `paginated_embed.py`
  уже резервировал этот более тяжёлый контрол именно за `/logs`, чтобы не раздувать кнопками
  простой пейджер, которым пользуются другие команды.
- `presentation/cogs/stats.py` — `StatsCog`: `/logs` (25 сделок/страница, без пейджера при
  единственной странице — как и остальные пагинируемые списки в проекте), `/day`/`/week`/`/month`
  (общий `_send_report`/`_render_period_pages`, 20 игроков/страница через `PaginatedEmbedView`,
  таблица игроков в моноширинном блоке). `/week`-валидация (диапазон ≤ 31 дня, конец не в
  будущем) — в самом cog'е, а не в `StatsService`, т.к. это специфика только одной команды
  (по аналогии с тем, как проверки `/add` живут в `TransactionsCog`, а не в `TransactionService`);
  `end >= start` дополнительно проверять не потребовалось — `DateRange.__post_init__` (M1) уже
  бросает `InvalidPeriodError` сам.
- `presentation/bot.py` — `StatsService` собран в `_setup_cache` (переиспользует уже созданные
  `transactions_repo` + новый `UsersCacheRepository(connection)`, по тому же паттерну, что и
  `ProfileService`), `StatsCog` зарегистрирован.
- `pyproject.toml` — два новых `per-file-ignores` (`RUF001-3`) для `application/dto/period_report.py`
  (докстринги дословно цитируют «у меня»/«мне» из листа) и `presentation/views/logs_pager.py`
  (кнопка `🔢 К странице» — часть UI, не опечатка), по уже устоявшемуся в проекте паттерну
  (`domain/enums.py`, `presentation/cogs/**` и т. д.).

Всего 667 тестов (было 616 до этапа), покрытие 95.34 % (`ruff`/`ruff format`/`mypy --strict`
чисты на 165 файлах). Живая проверка в Discord недоступна до перевыпуска `DISCORD_TOKEN`
(⛔ §17.4) — как и на всех предыдущих этапах.

**Найдено и исправлено в процессе:** первая реализация `parse_date()` через
`datetime.strptime(text, "%d.%m.%Y")` принимала двухзначный год (`"31.07.26"` → год `26` н. э.,
`ValueError` не срабатывает) и попутно давала `ruff DTZ007` (naive `datetime` без `%z`) —
переписано на ручной разбор с явной проверкой `len(year_text) == 4`, что и задокументировано
тестом `test_invalid[31.07.26]`.

---

## M8 — Ручные выдачи и `/tag` · `[x]` 100 %

- [x] `/set_referral` — поиск **первой (самой ранней)** строки игрока, запись в `H` только туда
- [x] `/set_referral` — отказ, если у игрока нет ни одной сделки
- [x] `/set_referral` — подтверждение перезаписи, если реферал уже указан
- [x] `/set_referral` — привязка Discord ID обоих + вызов прогрессии с `announce_to`
- [x] `/set_rank` — **только Discord-роль**, таблица не меняется
- [x] `/set_rank` — флаг `manual_rank_role` в `progression_state`, toggle при повторном вызове
- [x] Оба: эфемерный embed + публичное сообщение с тегами и пометкой «выдано вручную»
- [x] `/tag` — DM с embed'ом «🔔 Уведомление по тикету»
- [x] `/tag` — серая link-кнопка `🎫 Перейти к тикету` на `discord.com/channels/{guild}/{channel}`
- [x] `/tag` — фолбэк при закрытых DM (пинг в канале + предупреждение админу)

**DoD:** покрыто 26 новыми юнит-тестами (693 всего, было 667; покрытие 95.29 %, порог 85 %
пройден; `ruff`/`ruff format`/`mypy --strict` чисты на 174 файлах): роль выдаётся/снимается через
`RoleGateway.sync_roles` с `universe` в один тир лестницы (не задевает реферальные роли);
`manual_rank_role` выставляется/снимается в `progression_state` и подтверждено, что
`ProgressionService` (M3) уважает его при следующем `sync()`; повторный `/set_rank` с тем же
рангом определяется по факту владения ролью на живом `discord.Member` и переключает флаг;
`/set_referral` пишет `H` только в первую (по `sheet_row`) строку игрока, отказывает
(`NoTransactionsYetError`) при отсутствии сделок, запрашивает подтверждение перезаписи через
`ConfirmView` только если реферал реально меняется. Живая проверка выдачи роли/DM в Discord
недоступна до перевыпуска `DISCORD_TOKEN` (⛔ §17.4), как и на всех предыдущих этапах.

**Реализация:**
- `domain/errors.py` — новая `NoTransactionsYetError`; замаплена в `presentation/errors.py`.
- `domain/progression/ladder.py` — публичный `Ladder.by_key(key)`: нужен `/set_rank`, чтобы
  превратить значение choice (`tier.key`, стабильное) обратно в `RankTier`, не полагаясь на
  текст `label`, который `by_label` резолвит из формулы листа (то не то сопоставление, что нужно
  здесь — ручная выдача не связана с текущим значением `R`).
- `application/services/binding.py` — вынесенная из `TransactionService` (M4) функция
  `bind_discord()`: привязка Discord ID в колонку `I` понадобилась одинаково `/add` и
  `/set_referral`, дублировать её вторым приватным методом было бы нарушением DRY. Сама
  `TransactionService` теперь вызывает эту функцию вместо своего бывшего `_bind_discord`
  (поведение не изменилось — покрывающие его тесты прошли без правок).
- `application/dto/manual_grant.py` — `SetReferralResult`/`SetRankResult`.
- `application/services/manual_grants.py` — `ManualGrantService`: `set_referral()` (ищет первую
  строку через уже существующий `TransactionsCacheRepository.list_by_nick()`, ordered by
  `sheet_row` — переиспользование, отдельного `MIN(sheet_row)`-метода не потребовалось),
  `current_referrer()` (для проверки в cog'е до подтверждения перезаписи, чтобы UI-решение
  «спрашивать или нет» не пряталось внутри write-метода), `set_rank()` (universe для
  `RoleGateway.sync_roles` — вся ранговая лестница, не только целевой тир, иначе переключение с
  одного вручную выданного ранга на другой оставляло бы старую роль).
- `presentation/cogs/manual.py` — `ManualCog`: `/set_referral` (self-referral guard —
  та же проверка, что и в `/add`, для той же целостности данных; подтверждение перезаписи —
  тот же `ConfirmView`, что и confirm-rebind в `/add`), `/set_rank` (toggle определяется по
  фактическому владению ролью на переданном `discord.Member.roles`, без похода в Discord API —
  объект уже содержит текущие роли). Оба — триггеры `ProgressionService.sync(announce_to=...)`
  сразу после записи (PLAN.md §9.2 явно перечисляет оба как триггеры).
- `presentation/cogs/tag.py` — `TagCog`: без application-сервиса — команда не содержит бизнес-
  правил (только «отправить DM, а если закрыты — упомянуть в канале»), делегировать было бы
  нечему; такое решение уже применялось для чисто presentation-логики в проекте (например,
  пагинаторы) и не нарушает Clean Architecture (нет доменной логики для извлечения).
- `presentation/bot.py`/`_setup_cache` — `ManualGrantService` собран (свои экземпляры
  `ProgressionStateRepository`/`DiscordRoleGateway`, как и у `ProgressionService`), `ManualCog`
  и `TagCog` зарегистрированы.

**Найдено и исправлено в процессе:** не найдено — `TransactionService`'ный `_bind_discord`
был поведенчески идентичен нужной здесь логике, вынос в общий модуль прошёл без сюрпризов
(существующие тесты `test_transaction_service.py` прошли без изменений).

---

## M9 — Тикеты: продажа предметов и бустов · `[x]` 100 %

- [x] `on_guild_channel_create` — фильтр по 3 категориям, запись сессии
- [x] Ожидание сообщения Ticket Tool (`557628352828014614`) через `on_message` + фолбэк-таймер 30 с
- [x] Панель заявки: embed по типу + persistent кнопка `📝 Заполнить заявку`
- [x] Эфемерный выбор способа: `📬 Почта` / `🤝 Обмен` + шапка «Ник: Scaryyyyy»
- [x] Модал для `SELL_ITEMS` и `SELL_BOOSTS` (ник + 2 опциональных поля реферала)
- [x] Автосопоставление текстового ника реферала с участниками сервера
- [x] Итоговая карточка, редактируемая in-place; пустые поля не выводятся
- [x] Кнопка `📸 Прикрепить скриншот` + эфемерные требования к скриншоту
- [x] ★ Скриншот в карточку через `message.edit(attachments=[file])` + `attachment://` — ссылка не протухает
- [x] ★ Копия карточки со скриншотом внутри embed в лог-канал (`send(embed=..., file=...)`)
- [x] Валидация: ≤ 8 МБ, PNG/JPG/WEBP, понятная ошибка при превышении

### ★ Задел под OCR (делается здесь, работает с первого дня)
- [x] `application/ports/ocr.py` — порт `OcrGateway`
- [x] `infrastructure/ocr/null.py` — `NullOcrGateway` (возвращает `status=disabled`)
- [x] `domain/entities/screenshot.py` — DTO `ScreenshotImage`, `OcrResult`, `RecognizedItem`
- [x] `application/services/screenshots.py` — приём скриншота, `sha256`, запись
      в `screenshot_analyses`, вызов `OcrGateway.recognize()`
- [x] `infrastructure/ocr/samples.py` + флаг `OCR_KEEP_SAMPLES=true` —
      **★ сбор датасета в `data/ocr_samples/` с первого дня работы тикетов**
- [x] Требования к скриншоту сформулированы OCR-дружелюбно (полный экран, без обрезки
      и пережатия, окно сделки видно целиком) — это же и качество датасета
- [x] Сохранение оригинальных байтов без перекодирования
- [x] `render_ticket_card(session)` — карточка из состояния, готова принять OCR-блок
- [x] Модал суммы с параметром `default` (в v1.0 — `None`)

### Подтверждение
- [x] Админская кнопка `✅ Подтвердить` → модал суммы → `TransactionService.register()`
- [x] ★ Подтверждённая админом сумма пишется в `screenshot_analyses` как эталон —
      пара «скриншот → правильный ответ» для будущей настройки OCR
- [x] `ProgressionService.sync(..., announce_to=ticket_channel)` после подтверждения
- [x] Persistent views: детерминированные `custom_id`, `bot.add_view()` при старте
- [x] Восстановление состояния из `ticket_sessions` после рестарта

**DoD:** покрыто 56 новыми юнит-тестами (749 всего, было 693; покрытие 94.71 %, порог
85 % пройден; `ruff`/`ruff format`/`mypy --strict` чисты на 196 файлах). Полный путь
«создание канала → панель → форма → карточка → скриншот → подтверждение → строка в
таблице» прогнан по частям через сервисный и cog-слой на моках/реальной SQLite (сквозной
happy-path через реальный Discord недоступен до перевыпуска `DISCORD_TOKEN`, ⛔ §17.4, как
и на всех предыдущих этапах). Persistent views используют детерминированные `custom_id`
(`ticket:start:{kind}`, `ticket:screenshot`, `ticket:confirm`) и не хранят состояние на
инстансе — `TicketsCog.persistent_views()` регистрируется в `bot.add_view()` при старте,
проверено юнит-тестами, что обработчики читают состояние из `TicketService`/БД, а не из
объекта View. Скриншот архивируется в лог-канал (`send(embed=..., file=...)`, постоянная
CDN-ссылка сохраняется в `ticket_sessions.screenshot_url`) и одновременно перевыгружается
в саму карточку тикета (`message.edit(attachments=[file])`), embed которой ссылается на
него через `attachment://screenshot.png` — резервная копия не зависит от того, жив ли
лог-канал.

**Реализация:**
- `domain/enums.py` — `TicketStatus` (`awaiting_tool → awaiting_form → filled → confirmed`)
  и `DeliveryMethod` (`mail`/`trade`); `TicketSession.status`/`delivery_method` переведены
  с плоского `str` (заглушка M2) на эти enum'ы — ровно то расширение, которое M2 явно
  оставляла на этот этап. `infrastructure/cache/repositories/ticket_sessions.py` и
  существующий `test_ticket_sessions.py` (M2) обновлены под новый тип без изменения схемы.
- `domain/errors.py` — `TicketSessionNotFoundError` (стрелевое взаимодействие с несуществующей
  сессией — например, старая кнопка на удалённом канале); замаплена в `presentation/errors.py`.
- OCR-задел (§11.8): `domain/entities/screenshot.py` (`ScreenshotImage`, `RecognizedItem`,
  `OcrResult`), `application/ports/ocr.py` (`OcrGateway`), `infrastructure/ocr/null.py`
  (`NullOcrGateway`, `enabled=False`, `status=disabled`), `infrastructure/ocr/samples.py`
  (`save_sample` — пишет `<sha256>.<ext>` в `OCR_SAMPLES_DIR`, коллапсирует повторно
  присланные одинаковые скриншоты в один файл), `infrastructure/cache/repositories/
  screenshot_analyses.py` (`record` — upsert по `image_sha256`; `record_confirmed_amount` —
  проставляет `total_estimate` всем скриншотам тикета после подтверждения сделки, это и есть
  будущая обучающая пара «скриншот → верный ответ»), `application/services/screenshots.py`
  (`ScreenshotService.on_attached()` — хэш, опциональный сбор образца, безусловный вызов
  `OcrGateway.recognize()`, запись в кэш; `record_confirmed_amount()` — тонкая обёртка над
  репозиторием, вызывается из `TicketsCog._on_amount_submitted` после успешной записи сделки).
- `application/services/tickets.py` — `TicketService`: чистые чтение/запись `ticket_sessions`
  через набор узких мутаторов (`open_ticket` идемпотентен — повторное срабатывание
  `on_guild_channel_create`, например при реплее шлюзового события, не сбрасывает уже
  существующую сессию; `set_author`/`record_panel`/`record_delivery_method`/`record_form`/
  `record_summary_message`/`record_screenshot`/`record_confirmed`). Никакого Discord/embed-кода
  внутри — карточка тикета строится только `render_ticket_card(session)` из состояния,
  которое отдаёт этот сервис (соблюдён инвариант проекта).
- `presentation/cogs/tickets/` — новый пакет:
  - `card.py` — `render_ticket_card(session, embeds)`: единственное место сборки карточки;
    пустые поля (не заполненный ник, не выбранный способ, нет реферала) не выводятся.
  - `views.py` — `TicketPanelView` (persistent, один инстанс на `TicketKind`, кнопка
    `📝 Заполнить заявку`), `DeliveryMethodView` (обычный, эфемерный, живёт секунды),
    `TicketSummaryView` (persistent, `📸 Прикрепить скриншот` + `✅ Подтвердить`). Все
    обработчики — внешние callable, переданные в конструктор, а не методы вьюхи — вьюха
    не завязана на application-слой напрямую.
  - `modals.py` — `TicketFormModal` (ник + 2 опциональных поля реферала), `AmountModal`
    (одно поле суммы, принимает `default: str | None = None` — задел под M13, в v1.0 всегда
    `None`).
  - `cog.py` — `TicketsCog`: `on_guild_channel_create` (фильтр по `TICKET_CATEGORIES`, только
    `SELL_ITEMS`/`SELL_BOOSTS` — `ORDER_BOOSTS` сознательно не трогается, это M10) заводит
    сессию и **ждёт** `asyncio.Event`, выставляемый в `on_message` при появлении сообщения от
    Ticket Tool, с `asyncio.wait_for(..., timeout=30)` — «не спать фиксированное время»
    реализовано через event, а не `asyncio.sleep(30)`, фолбэк-таймер отрабатывает, только если
    Ticket Tool промолчал. Автор тикета определяется best-effort по permission overwrites
    свежесозданного канала (`_infer_author_id`) и при неудаче (`0`, невалидный snowflake)
    доопределяется по первому реальному взаимодействию (`_on_start`). Ник реферала в Discord
    сопоставляется с участником сервера через упоминание (`<@id>`) или точное совпадение
    имени/отображаемого имени (`_resolve_member`) — сервер маленький, полнотекстовый
    fuzzy-поиск избыточен. Подтверждение (`_on_confirm_button`/`_on_amount_submitted`)
    переиспользует `TransactionService.register()` без изменений — ключ идемпотентности
    `f"ticket:{channel_id}"` (не per-interaction, как у `/add`) дополнительно защищает от
    двойной записи при повторном сабмите модала для одного и того же тикета.
- `presentation/bot.py`/`_setup_cache` — `TicketService`/`ScreenshotService`/`TicketsCog`
  собраны (`NullOcrGateway()` подключён как единственная реализация `OcrGateway` в v1.0);
  persistent views регистрируются через `self.add_view(view)` для каждого вида из
  `TicketsCog.persistent_views()`.

**Осознанно не реализовано в этом этапе:** `ORDER_BOOSTS` — категория присутствует в
`TICKET_CATEGORIES` с M0, но `on_guild_channel_create` явно её игнорирует
(`_HANDLED_KINDS = {SELL_ITEMS, SELL_BOOSTS}`); заказ бустов и его редактор — весь M10.

**Найдено и исправлено в процессе:** отдельных находок в существующем коде не было (в отличие
от M2/M4) — этап целиком новый функционал. При написании тестов дважды поймана собственная
ошибка (не баг кода, а ошибка тестовых утверждений): дата в `_session()`-фикстурах бралась в
`UTC`, а `render_ticket_card` показывает время в `GMT+3` (`format_datetime`), из-за чего
`21:45` ожидаемо стало `00:45` следующего дня — поправлено в самом тесте; и обратная путаница
в `EmbedFactory` между `title`/`description` при проверке текста предупреждения о превышении
размера скриншота.

---

## M10 — Тикеты: заказ бустов · `[x]` 100 %

- [x] Модал `ORDER_BOOSTS` (ник + срок + 2 поля реферала)
- [x] `parse_deadline()` в бою: подсказка формата, валидация «в будущем, ≤ 90 дней», переоткрытие модала с сохранением полей
- [x] Постраничный выбор бустов (25 на страницу, выбор сохраняется между страницами)
- [x] Отметка уже выбранных с количеством (`✅ 🚀 Топот — 3 шт.`)
- [x] Редактор заказа — **одно** сообщение, редактируемое in-place
- [x] Select активной позиции + кнопки `➖` `➕` `🔢 Ввести количество` `🗑️ Удалить`
- [x] Модал количества с `parse_amount`, диапазон `1…9999`
- [x] Расчёт сумм из `price_sell`, пересчёт по актуальной цене при подтверждении
- [x] Подсветка изменения цены между выбором и подтверждением
- [x] Доступ к редактору: автор заявки + админы
- [x] Персистентность черновика в `boost_order_lines` (с `name_norm` + `category`)
- [x] `✅ Подтвердить заказ` (админ) → модал с предзаполненной суммой → сделка «Покупка (у меня)»

**DoD:** покрыто 63 новыми юнит-тестами (812 всего, было 749; покрытие 94.37 %, порог 85 %
пройден; `ruff`/`ruff format`/`mypy --strict` чисты на 202 файлах). Полный путь «заполнение
формы → добавление позиций → правка количества → подтверждение → строка в таблице» прогнан
через сервисный и cog-слой на реальной SQLite и моках Discord (сквозная проверка в живом
Discord недоступна до перевыпуска `DISCORD_TOKEN`, ⛔ §17.4). Черновик — обычная строка
`boost_order_lines`, переживает рестарт по конструкции (persistent-состояние уже было готово
с M2/M6); удаление предмета через `/del_item` не ломает открытый заказ — `CatalogService.
delete_item` (M6) уже перепривязывает/чистит строки черновика при перенумерации, `BoostOrderService.
list_lines_with_items` дополнительно устойчив к «осиротевшей» строке (просто пропускает её
при рендере, не падает).

**Реализация:**
- `infrastructure/cache/schema.sql`/`db.py` — новая колонка `ticket_sessions.active_order_item_id`
  (`SCHEMA_VERSION` 3→4, тот же паттерн, что и у M3/M4: идемпотентный DDL, реальных данных для
  бэкфилла ещё нет). Это единственный способ узнать, с какой строкой черновика работают кнопки
  `➖`/`➕`/`🔢`/`🗑️` редактора: у `OrderEditorView` один и тот же зарегистрированный persistent-
  инстанс обслуживает **все** каналы заказа бустов одновременно (детерминированные `custom_id`
  из §11.7 — `order:qty:plus` и т. п. — общие, не параметризованные по позиции), поэтому «какая
  строка сейчас активна» обязана жить в БД, а не в атрибуте инстанса вьюхи — иначе редактирование
  одного заказа било бы по состоянию другого.
- `domain/enums.py`/`application/dto/ticket_session.py` — без изменений сверх поля
  `active_order_item_id: int | None = None` (значение по умолчанию — существующие фикстуры и
  тесты не потребовали правок, кроме `test_ticket_sessions.py`, где raw-строки статусов заменены
  на `TicketStatus`-члены, см. заметку M9 — реально относится и к этому этапу, обе доработки шли
  рука об руку).
- `application/services/boost_orders.py` — `BoostOrderService`: реконсиляция мультиселект-страницы
  (`apply_page_selection` — трогает только позиции текущей страницы, что и даёт «выбор сохраняется
  при переключении страниц» без хранения состояния во вьюхе), `set_quantity`/`adjust_quantity`
  (клампинг `MIN_QUANTITY..MAX_QUANTITY`), `compute_total` (всегда читает `price_sell` из
  `ItemsCacheRepository` заново — «подсветка изменения цены» реализована через отсутствие
  устаревания как такового: сумма в карточке и в модале подтверждения всегда одна и та же живая
  цена, а не расхождение между «ценой на момент выбора» и «ценой на момент подтверждения», так
  как первая нигде не кэшируется).
- `presentation/cogs/tickets/order_card.py` — `render_order_editor(session, lines_with_items,
  embeds)`: заголовок `"🧾 Редактор заказа"` вместо заголовка панели — потребовало добавить
  необязательный параметр `title` в `EmbedFactory.ticket()` (единственное изменение в фабрике;
  инвариант «все embed'ы только через `EmbedFactory`» соблюдён).
- `presentation/cogs/tickets/order_views.py` — `OrderEditorView` (persistent, детерминированные
  `custom_id`: `order:select`, `order:qty:plus/minus/input/delete`, `order:add`, `order:confirm`)
  и `BoostMultiSelectView` (обычный, эфемерный, паджинированный мультиселект «➕ Добавить бусты» —
  **новый компонент, не переиспользование `PaginatedItemSelect`** из M6: тот однозначно
  single-select, а плану нужен мультиселект с чекбоксами и сохранением состояния между
  страницами; `PaginatedItemSelect` остался как есть для `/del_item`/`/price_list`/выбора
  активной позиции такого объёма, что в 25 не уместится, не потребовалось). Опции мультиселекта
  показывают текущее количество (`"🚀 Топот — 3 шт."`) через `quantities`, переданные из cog'а.
- `presentation/cogs/tickets/modals.py` — `OrderBoostsFormModal` (ник + срок + 2 поля реферала;
  при ошибке `parse_deadline` переоткрывается с текстом ошибки в подсказке поля и сохранёнными
  остальными полями) и `QuantityModal` (одно поле, тот же путь валидации, что и у количества
  через кнопки).
- `presentation/cogs/tickets/cog.py` — `TicketsCog` расширен: `_HANDLED_KINDS` теперь включает
  `ORDER_BOOSTS`; `_on_start` для этого вида сразу открывает модал заявки (без шага выбора
  способа передачи — заказ буста, в отличие от продажи, нечего «доставлять», это осознанное
  прочтение §11.3/§11.4, явно не описанное буквально ни там, ни там); весь орфографический блок
  «Boost-order editor» — обработчики select/qty-кнопок/добавления/подтверждения, каждый проходит
  через `_require_order_participant` (автор заявки **или** админ, PLAN.md §11.6 — до этой правки
  ограничение отсутствовало, добавлено при самопроверке). `_on_amount_submitted` (уже существовал
  с M9 для `/add`-эквивалента) переиспользован как есть для подтверждения заказа бустов —
  `_DEAL_TYPE_OF[ORDER_BOOSTS] = DealType.PURCHASE` и очистка `boost_order_lines` по завершении
  добавлены точечно, без дублирования пути записи сделки.
- `presentation/bot.py` — `BoostOrderService` собран и передан в `TicketsCog`; `TicketsCog` также
  получил явный `clock=SystemClock()` (нужен `_on_order_form_submitted` для `parse_deadline`).

**Найдено и исправлено в процессе:** при первичной реализации доступ к редактору заказа не был
ограничен (любой участник канала мог трогать чужой черновик) — упущение обнаружено на
самопроверке при сверке с PLAN.md §11.6 («Взаимодействовать с редактором может только автор
заявки (+ админы)»), исправлено добавлением `_require_order_participant` до того, как код был
зафиксирован; заодно добавлены тесты на отказ для «чужого» пользователя и на разрешение для
админа, не являющегося автором.

---

## M11 — Полировка и наблюдаемость · `[x]` 100 %

- [x] Вычитка всех пользовательских текстов, единообразие эмодзи во всех 19 командах
- [x] Финальная сверка чисел в UI с §9.1.1 (канон формул)
- [x] Проверка лимитов embed'ов на предельных данных (500 сделок, 200 предметов, 100 рефералов)
- [x] `/healthcheck` — состояние Sheets, кэша, задержка синка, uptime, остаток строк под формулами
- [x] `/healthcheck` — счётчик датасета OCR (`образцов / с эталонной суммой`), чтобы видеть
      готовность к M13 без ручного пересчёта файлов
- [x] Метрики раз в минуту: запросы к API, хитрейт кэша, очередь аудита
- [x] Graceful shutdown: дослать аудит, закрыть SQLite и сессию Discord

**DoD:** покрыто 34 новыми юнит-тестами (846 всего, было 812; покрытие 94.53 %, порог 85 %
пройден; `ruff`/`ruff format`/`mypy --strict` чисты на 207 файлах). Визуальное ревью всех 19
команд и 3 типов тикетов выполнено как текстовый аудит (grep по описаниям команд и UI-строкам,
не интерактивный клик по каждой — живая проверка в Discord недоступна до перевыпуска
`DISCORD_TOKEN`, ⛔ §17.4, как и на всех предыдущих этапах): все 17 админских команд
единообразно начинаются с `🛡️ [Админ] <эмодзи> <Текст>`, `/profile`/`/referrals` — без префикса
(ожидаемое исключение, PLAN.md §5.5); ни одно старое число из текстового описания
прогрессии (§9.1.1 «❌ Было») не встречается нигде в `src/` — сверено `grep`'ом по всему дереву,
не только по `presentation/`, как в частичной проверке M3. Лимиты embed'ов подтверждены не
только структурным аргументом (все постраничные списки строятся через `description`-текст, а
не `add_field`, так что 25-field cap Discord в принципе не может быть задет), но и прямыми
тестами на заявленных в плане величинах — 500 сделок → 20 страниц `/logs`, 200 предметов → 14
страниц `/price_list`, 100 рефералов → 7 страниц `/referrals`, для каждой страницы проверено
`len(embed) <= 6000` и `len(description) <= 4096`.

**Реализация:**
- `domain/clock.py` — `format_duration()`: компактный формат `"2 д 3 ч 15 мин"` для
  uptime/возраста кэша в `/healthcheck`.
- `infrastructure/sheets/client.py` — `read_request_count`/`write_request_count`: инкрементируются
  только после успешного (прошедшего retry) запроса, поэтому отклонённая `ProtectedRangeWriteError`
  запись не искажает метрику.
- `infrastructure/cache/sync.py` — `CacheSync.cache_hit_rate` (доля вызовов `ensure_fresh`,
  заставших кэш уже свежим; `None`, пока `ensure_fresh` ни разу не вызывался — отличать «нет
  промахов» от «нет данных»), `last_users_report`/`last_items_report` (последний `SyncReport`
  каждого цикла, включая прогон внутри `run_startup_sync`, — `/healthcheck` и лог метрик читают
  их напрямую, не гоняя отдельный синк).
- `application/services/audit.py` — `AuditService.queue_size()`: тонкий геттер над внутренней
  `asyncio.Queue`, без блокировки воркера.
- `infrastructure/cache/repositories/screenshot_analyses.py` — `count_all()`/
  `count_with_confirmed_amount()`: готовность датасета OCR (§11.8/M13 — минимум 150 образцов,
  ≥ 50 с эталонной суммой) считается прямыми `COUNT(*)`-запросами, без вычитывания строк в Python.
- `application/dto/health_status.py` + `application/services/health.py` — `HealthService`:
  единая точка сборки снапшота (Sheets-счётчики, hit-rate, последние отчёты синка, очередь
  аудита, счётчики датасета). Сознательно **не** содержит uptime и «жив ли бот прямо сейчас» —
  это состояние процесса (`StalbotBot`), не пул чисто прикладной логики; смешивать их сделало бы
  `HealthService` нетестируемым без реального Discord-подключения. Вместо отдельного булева
  «Sheets доступен» используется возраст последнего успешного синка кэша — тот же сигнал
  (устаревание = проблема с Sheets или синком), но не требует отдельного отслеживания ошибок
  фоновых `tasks.loop` через мутируемое состояние на `StalbotBot`.
- `presentation/cogs/health.py` — `HealthCog`: `/healthcheck` (админ-only, скрыта уже за счёт
  `@admin_only()`, отдельного флага «скрытая» не потребовалось — тот же механизм, что и у всех
  остальных админских команд). `started_at` передаётся в конструктор один раз при сборке в
  `_setup_cache` (сразу после успешного стартового синка) — счётчик uptime, а не поле
  `HealthService`.
- `presentation/bot.py` — заменена временная M1-команда `/ping` (её докстринг с самого начала
  и говорил «Superseded by `/healthcheck` in M11») на реальную регистрацию `HealthCog`;
  добавлен `_metrics_loop` (`tasks.loop`, 60 с) → `_run_metrics_log()`, логирующий тот же
  `HealthService.snapshot()`, что видит `/healthcheck`; `close()` дополнительно отменяет
  `_metrics_loop` (graceful shutdown теперь останавливает все четыре фоновых цикла, а не три).
- Тесты масштаба (не новый прод-код, а закрытие явного пункта DoD): `/logs` на 500 сделках,
  `/price_list` на 200 предметах, `/referrals` на 100 рефералах — каждая страница явно
  проверена на `len(embed) <= 6000`/`len(description) <= 4096`.

**Найдено и исправлено в процессе:** не найдено — числа UI прошли повторную сверку с
§9.1.1 без расхождений (ожидаемо: M3 уже проверяла их `grep`'ом, а M4–M10 не вводили новых
формуло-зависимых чисел, только переиспользовали уже существующие `RankLadder`/`ReferralLadder`).

---

## M12 — Документация, тесты, деплой · `[x]` 100 %

- [x] README: установка, настройка service account, права бота, intents, первый запуск
- [x] Ревизия docstrings по всем публичным API
- [x] Dockerfile (non-root) + `docker-compose.yml` + volume под `data/` и `credentials/`
- [x] `systemd`-юнит как альтернатива
- [x] Скрипт бэкапа SQLite + снапшот item database
- [x] CI (GitHub Actions): `ruff` → `mypy --strict` → `pytest --cov` (порог 85 %)
- [x] Итоговый прогон покрытия

**DoD:** чистая установка по README поднимает рабочего бота (проверено по чек-листу разделов
README — service account, права/intents бота, `.env`, первый запуск, `/healthcheck`; сквозной
живой прогон в реальном Discord по-прежнему недоступен до перевыпуска `DISCORD_TOKEN`, ⛔ §17.4,
как и на всех предыдущих этапах — это не блокирует установку по README, только финальную живую
проверку). CI зелёный локально тем же набором команд, что и в `.github/workflows/ci.yml`:
`ruff check .`, `ruff format --check .`, `mypy --strict`, `pytest --cov` — все четыре прошли
без замечаний (846 тестов, покрытие 94.53 %, порог 85 % пройден, 207 файлов).

**Реализация:**
- `README.md` — переписан с нуля (была одна заглушка «появится на M12»): установка (`venv`,
  extras `dev`/`ocr`), получение и подключение service account (с явным указанием единственного
  используемого scope `spreadsheets` из `infrastructure/sheets/client.py`), настройка Discord-
  приложения (privileged intents `members`/`message_content`, права бота, требование «роль бота
  выше выдаваемых ролей»), таблица обязательных переменных `.env`, первый запуск и что при этом
  происходит по шагам (валидация → `validate_layout()` → полный синк → `tree.sync()` →
  подключение), запуск в Docker, запуск как `systemd`-сервис, бэкапы, команды разработки/CI.
  Не документирует ничего, что ещё не реализовано (OCR-движки — явно помечены как задел под M13).
- `Dockerfile` — двухстадийная сборка (builder собирает wheel через `pip wheel .`, runtime на
  `python:3.12-slim` ставит только сам wheel), непривилегированный пользователь `stalbot`
  (uid/gid 1000), `data/`/`credentials/` создаются с нужным владельцем под будущие bind-mount'ы,
  `ENTRYPOINT ["python", "-m", "stalbot"]` — тот же вход, что и у консольного скрипта
  `stalbot` из `pyproject.toml::[project.scripts]`. `.dockerignore` добавлен отдельно — без
  него билд-контекст тянул бы `.venv/`, `.git/`, кэши линтеров и `data/`/`credentials/` с
  реальными секретами в контекст сборки.
- `docker-compose.yml` — один сервис, `env_file: .env`, том `./data:/app/data` (кэш, OCR-
  сэмплы) и `./credentials:/app/credentials:ro` (ключ service account монтируется read-only —
  контейнеру не нужно его писать). Живой `docker build`/`docker compose up` не прогнан в этой
  сессии — Docker Desktop недоступен в среде разработки (демон не запущен); Dockerfile проверен
  построчным ревью (multi-stage паттерн, non-root, ENTRYPOINT совпадает с рабочим `python -m
  stalbot`), а не сборкой — это открытая точка для проверки при первом реальном деплое.
- `deploy/stalbot.service` — systemd-юнит с комментарием в шапке, что именно нужно поменять
  перед установкой (пользователь, `WorkingDirectory`, путь к venv в `ExecStart`); базовое
  hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths` только на `data/`) —
  бот больше никуда не пишет на диске, кроме своего кэша.
- `scripts/backup.sh` — снимает `data/cache.sqlite3` через SQLite online backup API
  (`sqlite3 ... ".backup '...'"`, безопасно на живой базе, без остановки бота) в
  `backups/<UTC-timestamp>/cache.sqlite3`, ротация — оставляет последние 14 снапшотов
  (настраивается позиционным аргументом). Отдельного «экспорта item database» не заводилось —
  и снапшот удалённого предмета (`sync_meta.item_delete_backup`, §7.5), и вся таблица `items`
  уже внутри того же `cache.sqlite3`, второй файл дублировал бы работу. Источник истины —
  Google-таблица, поэтому бэкап SQLite нужен не для восстановления данных (это сделает полный
  синк при следующем старте), а чтобы не терять время на пересинк и накопленную историю
  `screenshot_analyses` (датасет OCR, §11.8) при сбое диска.
- `.github/workflows/ci.yml` — на `push`/`pull_request` в `main`/`dev`: `actions/setup-python`
  (3.12, с pip-кэшем) → `pip install -e ".[dev]"` → `ruff check` → `ruff format --check` →
  `mypy --strict` → `pytest --cov --cov-report=term-missing`. Порог покрытия 85 % не продублирован
  отдельным флагом — `fail_under = 85` уже задан в `pyproject.toml::[tool.coverage.report]` и
  `pytest-cov` сам проваливает прогон при недостижении (подтверждено локально: команда завершилась
  строкой `Required test coverage of 85.0% reached`).
- Ревизия docstrings: отдельного этапа переписывания не потребовалось — `ruff` с правилом `D`
  (Google convention, `pyproject.toml::[tool.ruff.lint]`) уже принудительно требует docstring на
  каждом публичном модуле/классе/функции с M0 и весь проект (207 файлов) проходит его чисто на
  момент этого этапа; точечно перечитаны ключевые публичные API (`bootstrap.py`,
  `application/ports/ocr.py`, `domain/progression/ladder.py` и другие порты/сервисы) — по
  содержанию и ясности замечаний не найдено, переписывать не потребовалось.

**Найдено и исправлено в процессе:** не найдено — этап был целиком новым для проекта
(README/Docker/systemd/бэкап/CI ранее не существовали), доработок в уже реализованном
функционале M0–M11 не потребовалось.

---

## M13 — OCR скриншотов · `[ ]` 0 % · *после v1.0*

> **Вход в этап заблокирован**, пока не выполнено условие:
> `data/ocr_samples/` ≥ 150 скриншотов, из них ≥ 50 с подтверждённой админом суммой.
> Копится автоматически с M9 — ориентировочно 2–4 недели живой работы бота.
> Без реальных данных движок настраивается вслепую, и этап растянется вдвое.

**Счётчик датасета:** `0 / 150` образцов · `0 / 50` с эталонной суммой
*(обновлять по факту; посмотреть можно через `/healthcheck`)*

- [ ] `tools/label_samples.py` — свести «скриншот ↔ подтверждённая сумма» в CSV-эталон
- [ ] Разделить выборку: обучающая (настройка) и отложенная 30 шт. (только для финальной проверки)
- [ ] `infrastructure/ocr/preprocess.py` — upscale ×2, grayscale, CLAHE, денойз,
      адаптивная бинаризация, опциональный кроп ROI
- [ ] Бенчмарк движков **на своём датасете**: `tesseract(rus+eng)` vs `paddleocr` vs Google Vision
- [ ] Метрики бенчмарка: точность суммы (±0 ₽), полнота по предметам, latency, стоимость вызова
- [ ] Выбор движка зафиксирован в ADR с цифрами, а не «по ощущениям»
- [ ] `infrastructure/ocr/matcher.py` — RapidFuzz `token_set_ratio ≥ OCR_MATCH_THRESHOLD`
- [ ] Приоритет категории по типу тикета (boost / resource)
- [ ] Извлечение количества: `×3`, `x3`, `3 шт`
- [ ] ★ Числа из распознанного текста — через **тот же** `domain/money.py`, второго парсера нет
- [ ] Расчёт `total_estimate` по item database + `confidence` + `warnings`
- [ ] Блок «🔍 Распознано автоматически» в карточке тикета
- [ ] Предзаполнение модала суммы (`default=total_estimate`)
- [ ] Подсветка расхождений между заявкой и распознанным
- [ ] Фоновое выполнение (`asyncio.create_task`), таймаут `OCR_TIMEOUT_SECONDS`
- [ ] Деградация: `ocr_status=failed` → карточка как в v1.0, ручной ввод
- [ ] Опциональный extra `pip install -e .[ocr]`; без него бот стартует на `NullOcrGateway`
- [ ] Регрессионный тест на «золотой» выборке с зафиксированным порогом точности

**DoD:** на отложенных 30 скриншотах сумма распознаётся верно в ≥ 80 % случаев;
при `OCR_ENABLED=false` поведение бота совпадает с v1.0; OCR **никогда** не пишет
в таблицу сам — только предлагает.

---

## Бэклог (после M13)

- [ ] Промокоды 🎩 Рекламного Барона (скидка 1.5 % / наценка 1 % + 🪙 10 новичку)
- [ ] Ежемесячный пассивный доход 👑 Legend (🪙 10 + 1 % от баланса, максимум 🪙 15)
- [ ] Команда `/calc` — встроенный калькулятор поверх `evaluate_amount()`
- [ ] Автоприменение скидок/наценок ранга при расчёте заказа бустов
- [ ] Экспорт статистики в CSV/XLSX
- [ ] Еженедельное закрепление объявления для 🧲 Вербовщика

---

## Журнал изменений

| Дата | Что сделано |
|------|-------------|
| 03.08.2026 | **M12 завершён — готовность v1.0 100 % (13/13 этапов).** `README.md` переписан с нуля: установка, получение и подключение service account, настройка Discord-приложения (intents/права/иерархия ролей), таблица обязательных `.env`-переменных, первый запуск, Docker, `systemd`, бэкапы, команды разработки/CI. `Dockerfile` (двухстадийная сборка, non-root пользователь `stalbot`) + `.dockerignore` + `docker-compose.yml` (тома `data/` и `credentials/:ro`) — билд не прогнан живьём (Docker Desktop недоступен в этой сессии), проверен построчным ревью. `deploy/stalbot.service` — systemd-юнит с hardening (`ProtectSystem=strict`, `ReadWritePaths` только на `data/`) как альтернатива Docker. `scripts/backup.sh` — SQLite online backup `cache.sqlite3` с ротацией последних 14 снапшотов; отдельный экспорт item database не заводился — таблица `items` и снапшот `/del_item` (`sync_meta.item_delete_backup`) уже внутри того же файла. `.github/workflows/ci.yml` — `ruff check` → `ruff format --check` → `mypy --strict` → `pytest --cov` на push/PR в `main`/`dev`; порог покрытия 85 % уже обеспечен `fail_under` в `pyproject.toml`, отдельный флаг не нужен. Ревизия docstrings не потребовала правок — `ruff` правило `D` уже принудительно держит их в порядке с M0, весь проект (207 файлов) чист. Итоговый локальный прогон: `ruff`/`ruff format`/`mypy --strict` чисты, 846 тестов пройдено, покрытие 94.53 % (порог 85 % пройден). **Найдено и исправлено:** не найдено — этап целиком новый (README/Docker/systemd/бэкап/CI ранее не существовали в проекте). Открытым остаётся только ⛔ перевыпуск `DISCORD_TOKEN` (§17.4) — блокирует не установку по README, а финальную живую проверку в реальном Discord; и вход в M13 (после v1.0), ожидающий накопления OCR-датасета. |
| 02.08.2026 | **M7 завершён.** `domain/clock.py::parse_date()` — строгий парсер `ДД.ММ.ГГГГ` без relative-фраз и без `datetime.strptime()` (ручной разбор, чтобы избежать naive `datetime`/`DTZ007`). `application/dto/{period_report,log_entry}.py` — `PeriodReport`/`PlayerPeriodStats` (вычисляемые `turnover`/`total_purchases`/`total_sales`/`net_profit`) и `LogEntry` (нумерация сделки внутри дня + `discord_id`, которого нет в `TransactionRecord`). `infrastructure/cache/repositories/transactions.py` — `count_all()`/`list_numbered_page()`: `day_number` через `ROW_NUMBER() OVER (PARTITION BY date(occurred_at) ORDER BY sheet_row)` в одном CTE-запросе до пагинации. `application/services/stats.py` — `StatsService.report(period)`: агрегация покупок/продаж по нику за `[00:00:00, 23:59:59.999999]` GMT+3 периода, сортировка по обороту. `presentation/views/logs_pager.py` — `LogsPagerView` (`⏮ ◀ n/m ▶ ⏭` + `🔢 К странице`, первый модал в проекте), заведён отдельно от `PaginatedEmbedView` — это место было явно зарезервировано под `/logs` ещё в M5. `presentation/cogs/stats.py` — `StatsCog`: `/logs` (25/страница), `/day`/`/week`/`/month` (общий рендер отчёта, 20 игроков/страница, `/week` валидирует диапазон ≤ 31 дня и «не в будущем» на уровне cog'а). `presentation/bot.py` — `StatsService`/`StatsCog` собраны в `_setup_cache`. Всего 667 тестов (было 616), покрытие 95.34 % (`ruff`/`ruff format`/`mypy --strict` чисты на 165 файлах). **Найдено и исправлено:** первая версия `parse_date()` через `strptime("%d.%m.%Y")` тихо принимала двухзначный год (`"31.07.26"` → год 26 н.э.) и попутно ловила `ruff DTZ007` — переписано на ручной разбор с проверкой длины года. |
| 02.08.2026 | **M6 завершён.** Домен/сервисы/инфраструктура этапа (`CatalogService`, `PricingService` с TXT-парсером и `group_price_changes`, `EmojiResolver`, `item_choices` автодополнение, `PaginatedItemSelect`, все DTO) уже существовали в рабочей копии на момент начала этапа — доведены до зелёного состояния без переделок. Реализован недостающий presentation-слой: `presentation/cogs/catalog.py` (`/item_add`, `/del_item`, `/price_list` с `_PriceListView` — переключение категории + пагинация, `/give_price`) и `presentation/cogs/pricing.py` (`/setprice`, `/setboost`, `/sync_prices`, `/new_price` с подтверждением через `ConfirmView`), плюс общий `render_price_change_report()` в `pricing.py`. `presentation/bot.py`: `EmojiResolver` подключён и обновляется в `on_ready`/`on_guild_emojis_update`, оба новых cog'а зарегистрированы в `_setup_cache`. Осознанно не реализован опциональный автозапуск `/sync_prices` после `/new_price` (явно необязателен по §10.6). `/del_item` использует единственный `item_id`-аргумент через автодополнение (как и `/setprice`/`/setboost`) вместо буквальной пары «название + опциональная категория» — Discord не поддерживает условно видимые опции, а `item_choices()` уже был спроектирован как общий для всех трёх команд. Всего 616 тестов, покрытие 94.93 % (`ruff`/`ruff format`/`mypy --strict` чисты на 157 файлах). |
| 02.08.2026 | **M5 завершён.** `application/services/profile.py` — `ProfileService` (`get_profile`/`list_referrals`), единая проверка привязки Discord ↔ ник (§10.2), переиспользуемая обоими методами. Новые домен-исключения `PlayerNotFoundError`/`ProfileAccessDeniedError` (`domain/errors.py`), замаплены в `presentation/errors.py`. `infrastructure/cache/repositories/transactions.py::list_referral_targets()` — реверс-индекс «кто кого пригласил» через `DISTINCT nick_norm WHERE referrer_norm = ?` (корректно работает с тем, что `H` пишется только на первую сделку реферала, M4). `domain/progression/ladder.py` получил публичный `threshold_of()` вместо доступа к приватному полю из presentation-слоя. `presentation/cogs/profile.py` — `ProfileCog`: `/profile` (Coins/XP/Ранг/Реф-роль/Приглашено, бонусы ранга из `RankLadder`, прогресс-бар до следующего) и `/referrals` (реф-роль + бонусы, список рефералов `ник → @tag`, прогресс, награда следующей роли) — единственные две команды без `@admin_only()`. `presentation/views/paginated_embed.py::PaginatedEmbedView` — минимальный Prev/Next-пейджер (15 рефералов/страницу), по образцу `ConfirmView`; сознательно не единый компонент с переходом по номеру страницы — это нужно только `/logs` (M7). Всего 537 тестов, покрытие 95.01 % (`ruff`/`ruff format`/`mypy --strict` чисты на 139 файлах). Ничего не найдено сверх плана — этап прошёл без сюрпризов относительно находок M2–M4. |
| 31.07.2026 | Создан план (`PLAN.md`) и трекер. Зафиксированы решения A1–A4. Выявлено 10 расхождений между текстом ТЗ и формулами + 4 ограничения Discord API. |
| 31.07.2026 | **Получены реквизиты.** Guild ID, Spreadsheet ID, ключ service account (`credentials/service_account.json`, проект `test-ds-bot`) — лежит по плану, перемещать не потребовалось. `.gitignore` проверен, секретов в git нет. ⛔ Токен бота передан открытым текстом → требует перевыпуска. |
| 31.07.2026 | **Добавлен задел под OCR** (решение A7). Порт `OcrGateway` + `NullOcrGateway`, DTO, таблица `screenshot_analyses` и сбор датасета `data/ocr_samples/` — всё в v1.0, начиная с M9. Новый этап **M13 — OCR** (2.5–3 д) с входным условием «≥ 150 образцов». Итого с OCR ~20.5 д. |
| 02.08.2026 | **M0 завершён.** Каркас проекта: `pyproject.toml` (deps, ruff `I/N/D/ANN/RUF/UP/B/DTZ/S`, mypy `--strict`, pytest, coverage ≥85 %), `.env.example` по §14, дерево пакетов `src/stalbot/{domain,application,infrastructure,presentation,config}`, `config/settings.py` (`pydantic-settings`, fail-fast), `config/ids.py` (категории тикетов, роли рангов/рефералов, `PARTNER_ROLE_ID`, Ticket Tool), `presentation/bot.py` + `__main__.py` + `bootstrap.py` (пустой бот), `.pre-commit-config.yaml`, минимальный `README.md`. `ruff check`, `ruff format --check`, `mypy --strict` — чисто. Запуск с плейсхолдер-токеном подтвердил сборку графа зависимостей вплоть до вызова Discord API (`401 Unauthorized` — ожидаемо, реальный токен не выпущен). Все docstrings и технические комментарии — на английском (решение §17.2 п.5); `ruff` поймал нарушение (`RUF002/RUF003` на кириллице в комментариях) — исправлено. |
| 02.08.2026 | **M4 завершён.** `application/services/transactions.py` — `TransactionService.register()`: идемпотентность через новую таблицу `write_idempotency` (`SCHEMA_VERSION` 2→3, ключ `str(interaction.id)`), поиск свободной строки в `Тикеты` по кэшу с однократной верификацией чтением (ограниченный ретрай при гонке), `write_verified` для `A:E`+`H` (без `F`/`G`), `H` пишется только на первой сделке игрока (иначе `СЧЁТЕСЛИ` в `P` задвоил бы рефералов), `read_until` с резервным `copyPaste(PASTE_FORMULA)` при выходе за протянутый диапазон формул, точечный рефреш через `CacheSync.sync_users_and_transactions()`, привязка Discord ID в `I` (с флагом принудительной перепривязки). `presentation/views/confirm.py` — переиспользуемый `ConfirmView` (Подтвердить/Отмена, заблокирован на автора, таймаут). `presentation/cogs/transactions.py` — первый настоящий cog проекта: `/add` (choice тип, ник, `discord` через `@app_commands.rename` — Python-параметр `discord_member`, чтобы не затенять модуль `discord`), валидация «реферал ≠ игрок», предупреждение при реферале без Discord, подтверждение конфликта привязки через `ConfirmView`, эфемерный отчёт и публичное сообщение с напоминанием об отзыве. Аудит-событие не потребовало нового кода — `on_app_command_completion` (M1) уже логирует любую успешную команду. `presentation/bot.py`/`bootstrap` дополнены сборкой `TransactionService` и регистрацией `TransactionsCog` в `setup_hook` до `tree.sync()`. Всего 510 тестов, покрытие 94.71 % (`ruff`/`mypy --strict` чисты на 132 файлах). Smoke-тест подтвердил регистрацию `/add` в дереве команд без реального подключения к Discord. **Найдено и исправлено:** конфликт имён тестовых модулей `test_transactions.py` (уже возникавший в M2 и «залатанный» переименованием) корневым образом устранён добавлением `__init__.py` во все директории `tests/`, а не точечными переименованиями. |
| 02.08.2026 | **M3 завершён.** `domain/progression/{ladder,ranks,referrals,perks}.py` — общий generic `Ladder[TierT]` (current/next/progress/perks_of/by_label/by_role_id/role_ids), `RankTier`/`RANKS` (пороги 50/300/1200/3500/7000 из `config.ids.RANK_ROLE_IDS`), `ReferralTier`/`REFERRAL_ROLES` (пороги 1/3/7/20/50), `perks.py` — только формуло-подтверждённые числа (разовые бонусы рангов/реф-ролей, бонус за крупную сделку, буст-бонус, XP-порог 250). Контрактный тест `test_ladder_matches_sheet_formula.py` сверяет пороги с замороженным снимком реальных формул `DataBase!R3`/`S3`. `application/ports/role_gateway.py` (`RoleSet`/`RoleDiff`/`RoleGateway`) + `infrastructure/discord/role_gateway.py` (`DiscordRoleGateway`, взаимоисключение внутри лестницы через `RoleSet.universe`, устойчив к `NotFound`/`Forbidden`/недоступной гильдии). `application/services/progression.py` — `ProgressionService.sync(nicks, *, announce_to=None)`: сверка ролей всегда, повышение объявляется только если предыдущее состояние существовало и новый тир строго выше (защита от даунгрейда/спама при первом синке), состояние пишется до отправки, поздравление — в `announce_to` или в лог-канал через `AuditGateway`, плюс запись в аудит. Флаг `manual_rank_role` (новая колонка в `progression_state`, `SCHEMA_VERSION` 1→2) — поллер полностью исключает ранговую лестницу из `sync_roles`, пока флаг не снят (готово к `/set_rank` в M8). `sync_booster_flag()` пишет колонку `Q` через уже защищённый `SheetsClient.batch_update` и пересинкает игрока. `presentation/bot.py`: `on_member_update` детектит смену буста, третий `tasks.loop` (`PROGRESSION_POLL_SECONDS=300`) гоняет фоновый поллер по всей базе. Всего 479 тестов, покрытие 94.81 % (`ruff`/`mypy --strict` чисты на 101 файле). Самопроверка нашла и исправила: (1) неверный AST-инвариант-тест из M2, ложно запрещавший легитимные вызовы `SheetsClient.batch_update()` извне — переписан на реальный риск (сырой `values_batch_update` gspread в обход защиты); (2) пропущенный в первом проходе пункт чек-листа `manual_rank_role`. Осознанно не реализовано: выдача роли 🤝 Партнёр по совокупному обороту рефералов (агрегация, не привязанная к чек-листу текущего этапа — отложена до M5/`/referrals`, которому нужен тот же реверс-индекс). |
| 02.08.2026 | **M2 завершён.** Sheets: `infrastructure/sheets/{a1,protection,ratelimit,client,layouts}.py` — A1-нотация (позиционная и квотированная, юникод-имена листов), `ensure_writable()` с исчерпывающим тестом по всем колонкам `DataBase` + AST-скан на посторонние вызовы `batch_update`, token-bucket рейт-лимитер с retry+backoff, `SheetsClient` (`batch_get`/`batch_update`/`write_verified`/`read_until`/`read_formula_extent`/`copy_formula_down`/`validate_layout`), `SYNC_LAYOUTS` и карта блоков `DataBase` с заголовками, снятыми **вживую** с реальной таблицы (реальное имя листа — `Мейн скуп`, не `Мейн Скуп`, как в тексте плана). Кэш: `infrastructure/cache/{schema.sql,db.py}` (полная схема §8.1, версия схемы в `sync_meta`), репозитории `items`/`users`/`transactions`/`progression_state`/`ticket_sessions`/`boost_order_lines`, `sync.py` (`CacheSync.run_startup_sync/sync_items/sync_users_and_transactions/ensure_fresh`, парсинг с устойчивостью к «грязным» историческим строкам). `presentation/bot.py`/`bootstrap.py` дополнены: `setup_hook` открывает кэш и обязательно синкает **до** регистрации команд, два `tasks.loop` с интервалами из `Settings`, предупреждения о нехватке формул уходят в лог-канал через `EmbedFactory`. Добавлены `domain/clock.py::parse_sheet_datetime()`, `domain/entities/{item,transaction,user_profile}.py`, `application/ports/clock.py`, `application/dto/{progression_state,ticket_session,boost_order_line}.py`, `SheetStructureError`/`ProtectedRangeWriteError` в иерархию исключений. Всего 418 тестов, покрытие 94.29 % (`ruff`/`mypy --strict` чисты). **Проверено вживую** (только чтение) против реальной таблицы: `validate_layout()` проходит, `run_startup_sync()` кладёт в SQLite 219 предметов / 237 пользователей / 64 сделки (554 исторические строки без даты корректно пропущены). Живая проверка поймала и позволила исправить два реальных бага: (1) `SheetsClient.batch_get` сопоставлял результат по эхо-строке `range`, а Google нормализует открытые диапазоны (`"A3:H"` → `"A3:H1598"`) — синк тихо писал 0 записей, юнит-тесты с фейком этого не ловили; исправлено на позиционное сопоставление; (2) мёртвый код в `_parse_items` (`_to_int()` никогда не возвращает `None`) пропускал проверку на пустой `id`. Также обнаружено (не баг бота, а реальное состояние таблицы): формулы `F`/`G` уже не покрывают весь диапазон сделок — бот корректно предупреждает, задокументировано как открытое действие заказчика. |
| 02.08.2026 | **M1 завершён.** Core-модули: `domain/errors.py` (иерархия `StalbotError`), `domain/money.py` (`parse_amount`/`evaluate_amount`/`format_amount`/`format_compact`, AST-калькулятор без `eval`, защита по длине/глубине/степени), `domain/clock.py` (`GMT3`, `SystemClock`, `DateRange`, `parse_deadline` с относительными и абсолютными форматами), `domain/nick.py` (`normalize_nick`), `domain/enums.py` (`DealType`, `ItemCategory`, `TicketKind` — `config/ids.py` обновлён на использование `TicketKind`). Presentation: `presentation/embeds/{palette,progress,factory}.py` (`EmbedFactory.success/info/warning/error/ticket/audit`, автообрезка под лимиты Discord + публичная `enforce_limits()`), `presentation/checks.py` (`@admin_only()`), `presentation/errors.py` (глобальный `on_app_command_error`, маппинг иерархии исключений на embed). Application/infrastructure: `application/dto/audit_event.py`, `application/ports/audit_gateway.py`, `application/services/audit.py` (очередь + фоновый воркер, батчинг до 10 embed'ов/сообщение, fallback в файловый лог), `infrastructure/discord/audit_channel.py`, `infrastructure/logging/{trace.py,setup.py}` (`contextvars`-трассировка trace_id, `structlog` → JSON stdout + ротация файла). `presentation/bot.py` дополнен: кастомный `CommandTree` с единым `on_error`, `on_app_command_completion` пишет `AuditEvent` в очередь, временная диагностическая `/ping` (будет заменена `/healthcheck` в M11). `bootstrap.py` собирает полный граф зависимостей (logging → EmbedFactory → Bot → AuditChannelGateway → AuditService). Всего 221 тест (113 — `money.py`, включая hypothesis-свойство round-trip), покрытие 91.53 % (порог 85 % пройден; домен ≥ 96 %, сервисы ≥ 98 %); `ruff check`/`ruff format --check`/`mypy --strict` чисты на 48 файлах. Инварианты (naive `datetime.now()`, прямые `discord.Embed()` вне фабрики) проверены grep'ом — нарушений нет. Живая проверка `/ping` в Discord отложена до перевыпуска токена (⛔ §17.4) — запуск бота подтверждён до границы `401 Unauthorized`. |
| 31.07.2026 | **Все вопросы закрыты заказчиком.** Добавлены решения A5 (формулы не трогать — бот их не пишет вообще, §7.3) и A6 (канон чисел = формулы, §9.1.1). Принято: `/set_rank` выдаёт только роль; `/set_referral` пишет в первую строку; прибыль = покупки − продажи; ID перенумеровываются; поздравления идут в канал события; скриншоты — в лог-канал через `attachment://` без отдельного архива; постраничный select; редактор заказа in-place. Роль 🤝 Партнёр: `1518584570457358556`. Блокеров не осталось. |
