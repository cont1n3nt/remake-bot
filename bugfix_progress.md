# bugfix_progress.md — трекер исправления багов

> Источник багов: [`bugfix.md`](bugfix.md). Этапы идут в порядке зависимостей архитектуры
> (domain → application → infrastructure → presentation), с этапом 0 вне очереди —
> критичные/security баги блокируют всё остальное по правилу stop-the-line скилла
> `debugging-and-error-recovery`.
>
> Статус: `not started` / `in progress` / `done`. «Review passed» проставляется скиллом
> `code-review-and-quality` после фикса (five-axis повторное ревью, не самопроверка автора фикса).
>
> Этап 0 закрыт 2026-08-05 — остальные этапы разблокированы.

---

## Этап 0 — Критично + Security (блокирует все остальные этапы)

Статус: **done**

- [x] CLUSTER-1 (APP-1 / TICK-1 / SEC-3 / TEST-1) — гонка двойной записи транзакции
- [x] INFRA1-1 — `ensure_writable()` fail-open на перевёрнутом диапазоне
- [x] DOM-1 — `evaluate_amount` float-роундтрип портит точность денег
- [x] APP-2 — `int()`-усечение расходится с закэшированным `Decimal` (5 мест)
- [x] INFRA1-2 — ложное несовпадение read-back стирает корректную запись
- [x] PRES-1 — утечка сырых сообщений исключений в error-embed

Заметки после исправления:
- **CLUSTER-1** (`application/services/transactions.py`, `presentation/cogs/tickets/cog.py`,
  `application/dto/transaction_request.py`): весь `TransactionService.register()` теперь
  сериализован одним `asyncio.Lock` (единственный экземпляр сервиса общий для `/add` и
  подтверждения тикетов — лок реально покрывает все вызывающие пути). `TicketsCog` получил
  двойную защиту: recheck статуса перед регистрацией (ловит «отстающий» повторный submit) +
  новый флаг `TransactionRegistrationResult.replayed` (ловит по-настоящему одновременный
  submit — проигравший вызов не дублирует объявления/скриншоты/progression-sync). Второй
  механизм добавлен по итогам обязательного ревью (см. ниже), не был в исходном плане.
  Прогнан через `security-auditor` — вердикт: фикс корректен, 0 critical/high; один new
  medium-finding (INFRA1-11) и один low (TICK-11, к моменту ревью уже исправлен) — см. ниже.
- **INFRA1-1** (`infrastructure/sheets/protection.py`): `ensure_writable` теперь поднимает
  `ValueError` на `end < start` вместо молчаливого пропуска проверки.
- **DOM-1** (`domain/money.py`): `evaluate_amount` больше не пропускает дробные денежные
  токены через `ast.parse`'s float-грамматику — они заменяются на плейсхолдер-идентификаторы
  (`__mN__`), резолвящиеся из отдельной `Decimal`-таблицы через новый `ast.Name`/`ast.Load` в
  whitelist. Целочисленные токены (включая операнды `**`) не тронуты — совместимо с
  существующей валидацией показателя степени.
- **APP-2** (`domain/money.py::round_for_storage` + 5 мест в `transactions.py`/`pricing.py`/
  `catalog.py`): везде, где раньше был `int(decimal)` (усечение) для записи в Sheets, теперь
  `round_for_storage(decimal)` (`ROUND_HALF_UP`, как `format_amount`) — и то же округлённое
  значение уходит и в Sheets, и в кэш/возвращаемый объект. Заодно поправлен смежный
  `PricingService.preview_import` (сравнивал сырое распарсенное значение с уже округлённым
  кэшем — ложно репортил «изменение» на строке, которая после округления не меняется);
  найдено ревью, исправлено сразу, не откладывалось.
- **INFRA1-2** (`infrastructure/sheets/client.py`): `write_verified` сравнивает write/read-back
  по ячейкам с right-padding короткого read-back до формы записанного (Sheets API обрезает
  висячие пустые ячейки/строки при чтении). В рабочем дереве уже была неверная попытка фикса
  (retry на «transient lag» — не тот диагноз, не адресовал реальную причину); заменена на
  фикс по правильному диагнозу из аудита, тесты переписаны под реальный сценарий.
- **PRES-1** (`presentation/errors.py`): сырой `str(cause)` теперь идёт пользователю только
  для `DomainError`; любой `InfrastructureError` (и всё немапленное вне `DomainError`)
  получает общее сообщение + trace_id, детали уходят в `logger.warning`.
- Новые баги, найденные по пути (см. `bugfix.md`): **INFRA1-11** (нет таймаута на Sheets-вызовы
  внутри залоченного `register()` — рост blast radius зависшего запроса; medium, отложено в
  Этап 3 к INFRA1-3) — **не исправлено, ждёт своего этапа**. **TICK-11** — исправлено сразу
  (см. CLUSTER-1 выше), не отложено.
- Все 864 unit-теста зелёные, `mypy`/`ruff` чисты по всему `src`+`tests`.
- Незакоммиченное `pyproject.toml` (пиннинг версий зависимостей) — не относится к Этапу 0,
  было в рабочем дереве до начала этой сессии, не тронуто.

Review passed: да (`agent-skills:code-reviewer`, five-axis; первый проход — «request changes»
на TICK-1/CLUSTER-1 companion fix, устранено добавлением `replayed`-флага; второй проход по
затронутым файлам — mypy/ruff/полный прогон тестов зелёные)

---

## Этап 1 — Domain (`domain/**`)

Зависит от: этапа 0 (DOM-1 уже фиксится там).
Статус: **not started**

- [x] DOM-1 — исправлено в Этапе 0
- [ ] DOM-2 / SEC-2 — `OverflowError` в `parse_deadline` от пользовательского текста
- [ ] DOM-3 — потеря точности выше 28 значащих цифр
- [ ] DOM-4 — `format_compact` неверная единица на границе округления
- [ ] DOM-5 — `parse_sheet_datetime` принимает некорректный 3-значный год
- [ ] DOM-6 — `Ladder.progress()` отрицательный `pct`
- [ ] DOM-7 — `Ladder.progress()` 100% на 1 единицу раньше
- [ ] DOM-8 / PRES-8 — неиспользуемый `PermissionError`, затеняющий builtin
- [ ] DOM-9 — (опционально) валидация монотонности порогов `Ladder`
- [ ] DOM-10 — (FYI, решение не обязательно) домен импортирует `config.ids`

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Этап 2 — Application (`application/**`)

Зависит от: этапа 1 (использует `domain/money.py` после фикса DOM-1/DOM-3).
Статус: **not started**

- [x] APP-1 — исправлено в Этапе 0 (CLUSTER-1)
- [x] APP-2 — исправлено в Этапе 0
- [ ] APP-3 — `/sync_prices` тихо обнуляет цену при непарсящейся ячейке
- [ ] APP-4 — `/del_item` не синкает `active_order_item_id`
- [ ] APP-5 — `add_item` race на устаревшем кэше
- [ ] APP-6 — N+1 обращения к кэшу (perf, не корректность)
- [ ] APP-7 — `set_quantity` без клэмпинга диапазона
- [ ] APP-8 — `ScreenshotService.on_attached` без try/except вокруг OCR

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Этап 3 — Infrastructure: Sheets + Cache

Зависит от: этапа 0 (INFRA1-1/INFRA1-2 там же) и этапа 2 (используется `application`-слоем).
Статус: **not started**

- [x] INFRA1-1 — исправлено в Этапе 0
- [x] INFRA1-2 — исправлено в Этапе 0
- [ ] INFRA1-3 — `retry_with_backoff` не ловит сетевые сбои вне `APIError`
- [ ] INFRA1-4 — `_to_int` тихо превращает мусорные `coins`/`xp` в `0`
- [ ] INFRA1-5 — наивная `bool()`-коэрсия `purchase`/`sale`
- [ ] INFRA1-6 — write-lock не покрывает полный read-modify-write (делит фикс с CLUSTER-1)
- [ ] INFRA1-7 — `_AcquireAll` не освобождает локи при отмене (latent)
- [ ] INFRA1-8 — docstring vs реализация: синк не инкрементальный
- [ ] INFRA1-9 — `SCHEMA_VERSION` не делает реальный `ALTER TABLE` (уже осознанно принято, low)
- [ ] INFRA1-10 — лок по листу целиком, не по блоку (informational)
- [ ] INFRA1-11 — (найдено при фиксе CLUSTER-1 в Этапе 0) нет таймаута на Sheets-вызовы
      внутри залоченного `TransactionService.register()` — зависший вызов теперь блокирует
      весь процесс, не только одного вызывающего. Делит корень с INFRA1-3.

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Этап 4 — Infrastructure: Discord / OCR / Logging / Config

Зависит от: этапа 1 (domain), может идти параллельно с этапом 3.
Статус: **not started**

- [ ] INFRA2-1 — `sync_roles()` ловит только `Forbidden`, обрывает весь поллер
- [ ] INFRA2-2 — grant/revoke в одном try/except врёт о частичном сбое (чинить вместе с INFRA2-1)
- [ ] INFRA2-3 — `trace_id` залипает в долгоживущих фоновых петлях
- [ ] INFRA2-4 — `save_sample()` не валидирует `extension` (latent path traversal)
- [ ] INFRA2-5 — OCR-сэмплы всегда `.png` независимо от реального формата
- [ ] INFRA2-6 — `log_level` не `Literal`
- [ ] INFRA2-7 — интервалы синка без `Field(gt=0)`
- [ ] INFRA2-8 — дублирующиеся константы статуса `"disabled"`
- [ ] INFRA2-9 — утечка fd при повторном `configure_logging()`

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Этап 5 — Presentation (non-ticket)

Зависит от: этапов 1-4 (cogs вызывают application/infrastructure).
Статус: **not started**

- [x] PRES-1 — исправлено в Этапе 0
- [ ] PRES-2 — `enforce_limits()` не гарантирует лимит 1024 на поле независимо от суммы
- [ ] PRES-3 — потенциально бесконечный цикл обрезки embed (latent DoS)
- [ ] PRES-4 — необработанный `NotFound`/`Forbidden` в `on_timeout()` × 5 мест (+ структурный рефактор в общий базовый класс)
- [ ] PRES-5 — мёртвый код `PaginatedItemSelect` (спросить перед удалением)
- [ ] PRES-6 — autocomplete не защищён runtime-проверкой (FYI, Discord-модель permissions — фактический барьер)
- [ ] PRES-7 — валидация `/week` в cog вместо domain/application слоя
- [ ] PRES-8 — см. DOM-8
- [ ] PRES-9 — `close()` не дожидается фоновых петель перед закрытием `cache_db` (FYI, требует подтверждения)

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Этап 6 — Presentation: Ticket flow

Зависит от: этапа 0 (TICK-1 там же), этапа 2 (application-сервисы тикетов/заказов), этапа 5 (общие view-паттерны, напр. PRES-4).
Статус: **not started**

- [x] TICK-1 — исправлено в Этапе 0 (CLUSTER-1)
- [ ] TICK-2 — навсегда неверно определённый автор тикета
- [ ] TICK-3 — нет проверки `TicketStatus.CONFIRMED` в обработчиках редактора заказа
- [ ] TICK-4 — `default` перекрывает `placeholder` с текстом ошибки в модалке дедлайна
- [ ] TICK-5 — >25 позиций в заказе крашит `Select`-виджет редактора
- [ ] TICK-6 — необработанное исключение при удалении канала во время ожидания Ticket Tool
- [ ] TICK-7 — гонка заполнения `_tool_wait` (минорная деградация, не крash)
- [ ] TICK-8 — нет guard на самоприглашение реферала (требует продуктового решения)
- [ ] TICK-9 — порядок ack/send в `_handle_change` (не подтверждено воспроизведением)
- [ ] TICK-10 — молчаливое усечение дробного количества в модалке
- [x] TICK-11 — исправлено в Этапе 0 вместе с CLUSTER-1/TICK-1 (флаг `replayed` +
      cog пропускает дублирующиеся side-effects/сообщения на проигравшем конкурентном submit)

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Этап 7 — Security (остаточные, не покрытые кластерами выше)

Может идти параллельно с этапами 1-6 после этапа 0.
Статус: **not started**

- [ ] SEC-1 — `evaluate_amount` принимает scientific notation → `Infinity`/`OverflowError` (тот же файл, что DOM-1/DOM-3 — рассмотреть в одном PR с этапом 1)
- [ ] SEC-2 — см. DOM-2 (этап 1)
- [ ] SEC-4 — общий `on_error` для всех `discord.ui.Modal` в проекте
- [ ] SEC-5 — cooldown на тяжёлых админ-командах (не срочно)
- [ ] SEC-6 — доп. sandboxing-директивы `deploy/stalbot.service` (info, не блокирует)

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Этап 8 — Test-coverage backfill (регрессионные тесты, не привязанные к конкретному фиксу выше)

Идёт параллельно с фиксами — часть тестов уже перечислена по конкретным багам в `bugfix.md`;
здесь — только структурные пробелы, не покрытые ни одним конкретным ID бага.
Статус: **not started**

- [ ] TEST-7 — тест на переживание рестарта persistent views (симуляция новой инстанции + чтение состояния из SQLite)
- [ ] TEST-8 — тест точности refill-математики rate limiter'а с управляемым/fake clock вместо реального времени
- [ ] TEST-6 — контрактный тест «побайтовой идентичности» поведения при `OCR_ENABLED=false`
- [ ] TEST-10 — оценить, стоит ли заводить хотя бы один настоящий VCR/fixture-based интеграционный тест против записанных ответов Sheets API (`tests/integration/` сейчас пуст)

Заметки после исправления:
_(заполнить)_

Review passed: ☐

---

## Сводка

| Этап | Модуль | Багов | Critical/Security | Статус |
|---|---|---|---|---|
| 0 | Критично + Security (кросс-модульно) | 6 | 6 | done |
| 1 | Domain | 10 | 1 (DOM-1 дублирован из эт.0) | not started |
| 2 | Application | 8 | 2 (APP-1/2 дублированы из эт.0) | not started |
| 3 | Infrastructure: Sheets+Cache | 10 | 2 (дублированы из эт.0) | not started |
| 4 | Infrastructure: Discord/OCR/Logging/Config | 9 | 0 (2 high) | not started |
| 5 | Presentation (non-ticket) | 9 | 1 (PRES-1 дублирован из эт.0) | not started |
| 6 | Presentation: Tickets | 10 | 1 (TICK-1 дублирован из эт.0) | not started |
| 7 | Security (остаточные) | 5 | 0 | not started |
| 8 | Test-coverage backfill | 4 | — | not started |

**Итого уникальных находок: ~55** (с учётом дублей CLUSTER-1 и DOM-8/PRES-8, DOM-2/SEC-2, посчитанных один раз).

Этап 0 исправлен и прошёл ревью (2026-08-05). Этап 1 (Domain) — следующий по очереди, ждёт
явной команды на продолжение (правило "один этап за раз").
