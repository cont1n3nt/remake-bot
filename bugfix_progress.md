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
Статус: **done**

- [x] DOM-1 — исправлено в Этапе 0
- [x] DOM-2 / SEC-2 — `OverflowError` в `parse_deadline` от пользовательского текста
- [x] DOM-3 — потеря точности выше 28 значащих цифр
- [x] DOM-4 — `format_compact` неверная единица на границе округления
- [x] DOM-5 — `parse_sheet_datetime` принимает некорректный 3-значный год
- [x] DOM-6 — `Ladder.progress()` отрицательный `pct`
- [x] DOM-7 — `Ladder.progress()` 100% на 1 единицу раньше
- [x] DOM-8 / PRES-8 — неиспользуемый `PermissionError`, затеняющий builtin
- [x] DOM-9 — (опционально, всё же сделано — напрямую защищает инвариант, на
      который опираются DOM-6/DOM-7) валидация монотонности порогов `Ladder`
- [x] DOM-10 — (FYI) домен импортирует `config.ids` — решение принято: не баг,
      оставлено как есть (см. bugfix.md)

Заметки после исправления:
- **DOM-2/SEC-2** (`domain/clock.py`): `_parse_relative_hours`/`_parse_relative_minutes`
  оборачивают `now + timedelta(...)` в `try/except OverflowError` → `DeadlineParseError`.
  Прогнано через `security-auditor` (нет других непокрытых мест с тем же паттерном, hint
  безопасен для показа юзеру, 50-символьный лимит модалки делает конверсию `int()` дешёвой
  даже на максимальной длине) — вердикт: чисто, 0 находок.
- **DOM-5** (`domain/clock.py`): `_ABSOLUTE_RE`'s год-группа `\d{2,4}` → `\d{4}|\d{2}`
  (ровно 2 или 4 цифры) — 3-значный год больше не проходит как валидная (но бессмысленная)
  дата ни в `parse_deadline`, ни в `parse_sheet_datetime`.
- **DOM-3** (`domain/money.py`): `parse_amount`/`evaluate_amount` теперь считают внутри
  `decimal.localcontext()` с `prec=60` вместо амбиентного дефолтного контекста (28 значащих
  цифр) — умножение на множитель (`ккк`/`кк`/`к`) и бинарные операции `evaluate_amount`
  больше не округляются молча выше 28 цифр.
- **DOM-4** (`domain/money.py`): `format_compact` эскалирует единицу измерения, если
  округлённое частное достигает 1000 (напр. `999950` → `1 кк`, не `1000 к`); цикл
  поддерживает каскадную эскалацию через несколько уровней подряд.
- **DOM-6/DOM-7** (`domain/progression/ladder.py`): `Ladder.progress()` теперь
  `pct = max(0, min(99, round(...))) if need > 0 else 99` — нижняя граница на случай
  отрицательного `value`, верхняя граница 99 (не 100) пока следующий тир ещё впереди.
- **DOM-9**: `Ladder.__init__` валидирует строго возрастающие пороги, поднимает `ValueError`
  иначе — защищает именно тот инвариант (`need > 0`), от которого зависит фикс DOM-6/DOM-7.
- **DOM-8/PRES-8**: `domain/errors.py::PermissionError` удалён (0 использований в `src`/`tests`,
  затенял builtin; permission-проверки идут через `app_commands.CheckFailure`).
- Прогнан `agent-skills:code-reviewer` (five-axis) по всему диффу этапа — вердикт **APPROVE**,
  0 critical/required; 2 optional-комментария (заметить, что ветка `need <= 0` в
  `Ladder.progress()` сейчас недостижима для обеих реальных лестниц; заметить, что цикл
  эскалации в `format_compact` полагается на порядок `_COMPACT_UNITS` largest-first) —
  оба добавлены как комментарии в код.
- Все 884 unit-теста зелёные (+20 к концу Этапа 0), `mypy`/`ruff` чисты по всему `src`+`tests`.
- Новых багов по пути не найдено.

Review passed: да (`agent-skills:code-reviewer`, five-axis, APPROVE после добавления двух
optional-комментариев; `security-auditor` отдельно на DOM-2/SEC-2)

---

## Этап 2 — Application (`application/**`)

Зависит от: этапа 1 (использует `domain/money.py` после фикса DOM-1/DOM-3).
Статус: **done**

- [x] APP-1 — исправлено в Этапе 0 (CLUSTER-1)
- [x] APP-2 — исправлено в Этапе 0
- [x] APP-3 — `/sync_prices` тихо обнуляет цену при непарсящейся ячейке
- [x] APP-4 — `/del_item` не синкает `active_order_item_id`
- [x] APP-5 — `add_item` race на устаревшем кэше
- [x] APP-6 — N+1 обращения к кэшу (perf, не корректность) — сделано, хоть и
      Suggestion-уровня: чётко ограничено, каждый вызывающий сайт получил тест
- [x] APP-7 — `set_quantity` без клэмпинга диапазона
- [x] APP-8 — `ScreenshotService.on_attached` без try/except вокруг OCR

Заметки после исправления:
- **APP-3** (`application/services/pricing.py`, `application/dto/sync_prices_report.py`,
  `presentation/cogs/pricing.py`): `_cell_decimal` больше не глотает `InvalidOperation` —
  непарсящаяся (не пустая) ячейка репортится в новом поле `SyncPricesReport.unparseable`
  и пропускается без записи, вместо генерации `PriceChange`, обнуляющего цену. Cog
  показывает список отдельно от `not_found`.
- **APP-4** (`application/services/catalog.py`,
  `infrastructure/cache/repositories/ticket_sessions.py`, `presentation/bot.py`):
  `CatalogService` получил новую зависимость `TicketSessionsRepository` (по той же
  причине, по которой там уже был `BoostOrderLinesRepository` — обе хранят указатель на
  `item_id`, которому нужна перепрошивка при ренумерации). Новые методы
  `reassign_active_order_item`/`clear_active_order_item_for` зеркалят существующий паттерн
  `boost_order_lines.reassign_item_id`/`delete_by_name`. **Важный порядок**: clear для
  удалённого id идёт ПЕРЕД циклом ренумерации-переприсвоения — иначе он стирает сессию,
  только что корректно переприсвоенную на тот же (переиспользованный) id. Поймано
  собственным regression-тестом в процессе (red перед фиксом порядка, green после).
- **APP-5** (`application/services/catalog.py`): `add_item` читает живой блок листа
  (`_read_block()`), как `delete_item`, а не кэш — и для id/row, и для duplicate-check
  (один и тот же read на обе цели).
- **APP-7** (`application/services/boost_orders.py`): `set_quantity` клэмпит к
  `MIN_QUANTITY..MAX_QUANTITY`, как уже делает `adjust_quantity`.
- **APP-8** (`application/services/screenshots.py`): `on_attached` оборачивает
  `OcrGateway.recognize()` в `try/except Exception` (не `BaseException` — `CancelledError`
  должен продолжать всплывать), деградирует в `OcrResult(status="failed", error=...)`,
  логирует warning.
- **APP-6** (2 репозитория + 4 сервиса): новые батч-методы `ItemsCacheRepository.get_by_ids`,
  `UsersCacheRepository.get_by_nicks`/`get_nick_displays`; заменили N+1 циклы в
  `BoostOrderService.list_lines_with_items`, `PricingService.apply_import`,
  `StatsService.report`, `ProfileService.list_referrals`. `# noqa: S608` на построение
  `IN (...)` — обоснование (интерполируется только количество `?`-плейсхолдеров, не данные)
  независимо проверено ревью.
- Прогнан `agent-skills:code-reviewer` (five-axis) по всему диффу этапа — вердикт
  **APPROVE**, 0 critical/required. Один not-blocking finding (неограниченный `IN(...)` —
  залогирован как **INFRA1-12**, отложено в Этап 3) и две мелкие suggestion-заметки
  (добавлены как комментарии в код сразу).
- Все 896 unit-теста зелёные (+7 к концу Этапа 1), `mypy`/`ruff` чисты по всему `src`+`tests`.
- Новых блокирующих багов не найдено; один low/FYI найден и отложен (INFRA1-12, см. выше).

Review passed: да (`agent-skills:code-reviewer`, five-axis, APPROVE)

---

## Этап 3 — Infrastructure: Sheets + Cache

Зависит от: этапа 0 (INFRA1-1/INFRA1-2 там же) и этапа 2 (используется `application`-слоем).
Статус: **done**

- [x] INFRA1-1 — исправлено в Этапе 0
- [x] INFRA1-2 — исправлено в Этапе 0
- [x] INFRA1-3 — `retry_with_backoff` не ловит сетевые сбои вне `APIError`
- [x] INFRA1-4 — `_to_int` тихо превращает мусорные `coins`/`xp` в `0`
- [x] INFRA1-5 — наивная `bool()`-коэрсия `purchase`/`sale`
- [x] INFRA1-6 — write-lock не покрывает полный read-modify-write (делит фикс с CLUSTER-1)
- [x] INFRA1-7 — `_AcquireAll` не освобождает локи при отмене (latent)
- [x] INFRA1-8 — docstring vs реализация: синк не инкрементальный — решение принято:
      не баг (см. заметки), докстринг поправлен
- [x] INFRA1-9 — `SCHEMA_VERSION` не делает реальный `ALTER TABLE` — не баг, уже осознанно
      принято (изменений не вносилось)
- [x] INFRA1-10 — лок по листу целиком, не по блоку — informational, не баг, оставлено как есть
- [x] INFRA1-11 — нет таймаута на Sheets-вызовы внутри залоченного пути. Делит корень с INFRA1-3.
- [x] INFRA1-12 — неограниченный `IN (...)` в батч-методах — задокументировано в докстрингах
      (не чанковано — недостижимо сегодняшними вызывающими, low/FYI)

Заметки после исправления:
- **INFRA1-3/INFRA1-11** (`infrastructure/sheets/ratelimit.py`): `retry_with_backoff` теперь
  ловит `requests.exceptions.RequestException` (сетевые сбои ниже `gspread`'s `APIError` —
  DNS, reset, TLS) в дополнение к `APIError`, но только транзиентное подмножество
  (`ConnectionError`/`Timeout`/`ChunkedEncodingError` — `_RETRYABLE_TRANSPORT_ERRORS`);
  не-транзиентные (`MissingSchema` и т.п. — баг конфигурации, не сети) падают сразу, без
  ретраев. Каждая попытка обёрнута в `asyncio.wait_for(..., timeout=REQUEST_TIMEOUT_SECONDS)`
  (20с) — закрывает INFRA1-11 (зависший вызов больше не блокирует лок навечно). Добавлен
  новый `ReentrantAsyncLock` (task-identity + depth counter) — нужен для INFRA1-6.
- **INFRA1-6** (`infrastructure/sheets/client.py::SheetsClient.locked()`,
  `application/services/catalog.py`): публичный реентерабельный лок по листу — вызывающий
  оборачивает весь read→compute→write (не только сетевой вызов, как раньше). `CatalogService
  .add_item`/`delete_item` теперь держат его на весь метод (у `delete_item` — включая
  reassignment сессий/boost-order-lines, изначально это было упущено при первом проходе
  фикса, поймано ревью — см. ниже). Также `SheetsClient._gspread_client()` ставит
  `gspread`'s `HTTPClient.set_timeout(REQUEST_TIMEOUT_SECONDS)` — транспортный таймаут,
  который реально прерывает зависший сокет (не только `asyncio.wait_for`, который лишь
  бросает поток, а тот может завершиться позже и затереть более новую запись).
- **INFRA1-7** (`infrastructure/sheets/client.py::_AcquireAll`): `__aenter__` теперь
  try/except с освобождением уже взятых локов перед re-raise.
- **INFRA1-4** (`infrastructure/cache/sync.py`): новый `_to_int_strict` (возвращает `None`
  на непустой-но-непарсящийся `coins`/`xp`, `0` на пустую ячейку). Для Тикеты — строка
  пропускается (как `amount`, уже был прецедент). Для Юзеры — **профиль не пропускается**
  (первая версия фикса пропускала весь профиль — ревью поймало, что `replace_all`'s полный
  wipe стирает заодно оборот/ранг/реф-роль/Discord-биндинг, что хуже временного нуля на
  одном поле; исправлено — профиль сохраняется, только `coins`/`xp` падают в `0` с громким
  warning). `_to_int`/`_to_int_strict` также защищены от `nan`/`inf` (`int(nan/inf)`
  падает необработанным иначе — тоже поймано ревью).
- **INFRA1-5** (`infrastructure/cache/sync.py`): `_to_bool` — строгий whitelist truthy-строк
  вместо `bool(value)` (`bool("FALSE") == True` в чистом Python).
- **INFRA1-8**: докстринг `transactions.py`-репозитория утверждал, что синк инкрементальный;
  реальность — `CacheSync` перечитывает весь диапазон Тикеты каждый цикл. Решение: не
  корректность (upsert идемпотентен), не менять диапазон чтения ради риска новых edge-кейсов
  вокруг formula-extent проверки, читающей тот же блок — докстринг приведён в соответствие
  с реальным поведением.
- **INFRA1-9/INFRA1-10**: без изменений кода, отмечены как рассмотренные (не баги).
- **INFRA1-12**: докстринги `get_by_ids`/`get_by_nicks`/`get_nick_displays` документируют
  предел `SQLITE_MAX_VARIABLE_NUMBER`, чанкинг не добавлен (недостижимо).
- Обязательный `agent-skills:code-reviewer` прогонялся **дважды**: первый проход — **request
  changes** (1 critical: INFRA1-4's первая версия стирала весь профиль пользователя через
  `replace_all`; 3 important: `delete_item`'s reassignment-цикл оставался вне лока,
  `asyncio.wait_for` без транспортного таймаута оставляет риск отложенной перезаписи от
  осиротевшего потока, `ReentrantAsyncLock` не документировал latent deadlock-ловушку с
  `asyncio.wait_for`'s child-task). Все 4 исправлены; второй проход подтвердил все фиксы
  (плюс добавлен недостающий тест на `set_timeout`, отмеченный ревью как непокрытый).
  Regression-тесты на конкурентность (`add_item`/`delete_item`) сначала были too weak
  (проходили даже без фикса) — переписаны на прямое наблюдение порядка событий
  (`read_start`/`write_end`/`reassign_end`), вручную проверено red→green на обеих.
- Все 925 unit-тестов зелёные (+14 к концу Этапа 2), `mypy --strict`/`ruff` чисты по
  всему `src`+`tests`.
- Новых блокирующих багов не найдено.

Review passed: да (`agent-skills:code-reviewer`, five-axis; первый проход — request changes
на INFRA1-4/INFRA1-6/`ReentrantAsyncLock`-документации, все устранены; второй проход —
подтверждено, плюс закрыт один тест-гэп, найденный при верификации)

---

## Этап 4 — Infrastructure: Discord / OCR / Logging / Config

Зависит от: этапа 1 (domain), может идти параллельно с этапом 3.
Статус: **done**

- [x] INFRA2-1 — `sync_roles()` ловит только `Forbidden`, обрывает весь поллер
- [x] INFRA2-2 — grant/revoke в одном try/except врёт о частичном сбое (чинить вместе с INFRA2-1)
- [x] INFRA2-3 — `trace_id` залипает в долгоживущих фоновых петлях
- [x] INFRA2-4 — `save_sample()` не валидирует `extension` (latent path traversal)
- [x] INFRA2-5 — OCR-сэмплы всегда `.png` независимо от реального формата
- [x] INFRA2-6 — `log_level` не `Literal`
- [x] INFRA2-7 — интервалы синка без `Field(gt=0)`
- [x] INFRA2-8 — дублирующиеся константы статуса `"disabled"`
- [x] INFRA2-9 — утечка fd при повторном `configure_logging()`

Заметки после исправления:
- **INFRA2-1/INFRA2-2** (`infrastructure/discord/role_gateway.py::sync_roles`): grant и
  revoke теперь два независимых `try/except discord.HTTPException` (было: один общий
  `try/except discord.Forbidden` вокруг обоих) — транзиентный `5xx`/удалённая роль больше
  не роняет весь фоновый поллер по всей базе игроков (`HTTPException` — родитель
  `Forbidden`, так что покрытие не сузилось), и частичный сбой (grant прошёл, revoke упал)
  теперь корректно репортится в `RoleDiff`, а не маскируется под «ничего не изменилось».
- **INFRA2-3** (`presentation/bot.py`, `application/services/audit.py`): `set_trace_id()`
  был определён, но нигде не вызывался в проде — `current_trace_id()`'s фоллбек кэширует
  сгенерированный id в контекстваре навечно для долгоживущего `asyncio.Task`. Для команд
  это не проблема (каждый interaction — свежий Task), но 4 `tasks.loop`-цикла в `bot.py` и
  воркер `AuditService._run()` — каждый один и тот же `Task` весь процесс. Добавлен
  `set_trace_id(new_trace_id())` в начало каждого тика/итерации.
- **INFRA2-4** (`infrastructure/ocr/samples.py`): `save_sample()` валидирует `extension`
  через `re.fullmatch(r"[A-Za-z0-9]{1,8}", extension)`, поднимает `ValueError` иначе —
  defense-in-depth на границе функции, недостижимо сегодняшним единственным вызывающим.
- **INFRA2-5** (`application/services/screenshots.py`): новый `_sample_extension()` берёт
  расширение из `mime` (allowlist `_MIME_EXTENSIONS`) в приоритете над именем файла,
  которое используется только как fallback для нераспознанного mime. `mime` очищается от
  `; charset=...`-параметров перед поиском в allowlist — поймано ревью на первом проходе
  (без очистки любой параметризованный mime всегда промахивался мимо словаря и тихо
  откатывался на имя файла, воскрешая тот же баг, который чинит INFRA2-5).
- **INFRA2-6/INFRA2-7** (`config/settings.py`): `log_level` теперь `Literal["DEBUG", ...,
  "CRITICAL"]`; `sync_users_interval_seconds`/`sync_items_interval_seconds`/
  `progression_poll_seconds` теперь `Field(gt=0)` — опечатка/`0`/отрицательное значение
  ловится на старте, а не позже нечитаемой ошибкой/hot loop.
- **INFRA2-8** (`domain/entities/screenshot.py`): новая `OCR_STATUS_DISABLED` — общий
  источник истины вместо двух независимых `"disabled"`-констант в `infrastructure/ocr/
  null.py` и `application/services/tickets.py`.
- **INFRA2-9** (`infrastructure/logging/setup.py`): `configure_logging()` закрывает уже
  подключённые хендлеры перед переприсвоением `root.handlers` — `RotatingFileHandler`
  держит открытый fd, который раньше терялся без `.close()`.
- Обязательный `agent-skills:code-reviewer` — **APPROVE** с первого прохода (один
  important-finding: `_sample_extension()`'s mime-lookup не чистил `; charset=...`-параметр,
  из-за чего параметризованный mime всегда промахивался мимо `_MIME_EXTENSIONS` и тихо
  откатывался на имя файла — практически возвращало баг INFRA2-5; исправлено сразу).
  Два suggestion-уровня findings залогированы как новые баги, не исправлены (см. ниже) —
  требуют либо более широкого рефакторинга (per-player trace_id внутри `sync()`), либо
  продуктового решения (что делать с частичным сбоем grant/revoke на уровне выше
  `sync_roles`), оба не блокируют текущий этап.
- Все 951 unit-тест зелёные (+26 к концу Этапа 3), `mypy --strict`/`ruff` чисты по
  всему `src`+`tests`.
- Новые баги, найденные по пути (см. `bugfix.md`): **INFRA2-10** (гранулярность
  trace_id внутри `_run_progression_poll` — весь тик, а не на игрока, low/Suggestion) и
  **INFRA2-11** (уточнённый `RoleDiff` из фикса INFRA2-2 нигде не используется вызывающим
  кодом, low/Suggestion) — оба не исправлены, ждут своего повода (первый — более широкого
  трейсинг-рефакторинга, второй — продуктового решения о реакции на частичный сбой).

Review passed: да (`agent-skills:code-reviewer`, five-axis, APPROVE после исправления
одного important-finding по ходу; mypy/ruff/полный прогон тестов зелёные)

---

## Этап 5 — Presentation (non-ticket)

Зависит от: этапов 1-4 (cogs вызывают application/infrastructure).
Статус: **done**

- [x] PRES-1 — исправлено в Этапе 0
- [x] PRES-2 — `enforce_limits()` не гарантирует лимит 1024 на поле независимо от суммы
- [x] PRES-3 — потенциально бесконечный цикл обрезки embed (latent DoS)
- [x] PRES-4 — необработанный `NotFound`/`Forbidden` в `on_timeout()` × 5 мест (+ структурный рефактор в общий базовый класс)
- [x] PRES-5 — мёртвый код `PaginatedItemSelect` — подтверждено пользователем 2026-08-05, удалено
- [x] PRES-6 — autocomplete не защищён runtime-проверкой — не баг, FYI подтверждён, действий не требуется
- [x] PRES-7 — валидация `/week` в cog вместо domain/application слоя
- [x] PRES-8 — см. DOM-8 (уже исправлено в Этапе 1, дубликат)
- [x] PRES-9 — `close()` не дожидается фоновых петель перед закрытием `cache_db` — подтверждено воспроизведением, исправлено

Заметки после исправления:
- **PRES-2/PRES-3** (`presentation/embeds/factory.py`): `enforce_limits()` теперь безусловно
  клэмпит `field.value`/`field.name` к 1024/256 сразу после ограничения числа полей — до
  проверки суммарной длины (закрывает PRES-2). Цикл обрезки по суммарной длине останавливается,
  как только значение выбранного (самого длинного) поля уже стало плейсхолдером `"—"` — раньше
  тот же вызов `set_field_at` повторялся бесконечно (закрывает PRES-3, реальный infinite loop,
  воспроизведён напрямую: `python repro.py` с 25 полями по 256-символьному имени и значением
  `"—"` не завершался за 8с до фикса, мгновенно завершается после).
- **PRES-4** (новый `presentation/views/base.py::AuthorLockedView`, `confirm.py`,
  `logs_pager.py`, `paginated_embed.py`, `cogs/catalog.py::_PriceListView`): общий базовый
  класс вместо 4 независимых копий `interaction_check`/`on_timeout` (5-е место, `_PriceListView`
  из PRES-4 включало и то, что стало PRES-5-кандидатом, `PaginatedItemSelect` — тоже
  унаследовано от базового класса перед удалением). `on_timeout` теперь ловит
  `discord.NotFound`/`discord.Forbidden` вокруг `message.edit(...)` — раньше падало
  необработанным, если сообщение удалено или бот потерял доступ к каналу до истечения таймаута.
- **PRES-5**: `PaginatedItemSelect` удалён (вместе с тестами) — пользователь подтвердил, что
  ничего в `src/` на него не ссылается (M10 использовал собственный пикер).
  `AuthorLockedView`'s докстринг обновлён, ссылка на класс убрана.
- **PRES-6**: рассмотрено, не баг — реальный барьер — Discord-модель permissions
  (`default_member_permissions`), не runtime-проверка autocomplete; действий не требуется.
- **PRES-7** (`domain/clock.py::DateRange.week`, `presentation/cogs/stats.py`): бизнес-правило
  `/week` (диапазон ≤31 дня, конец не в будущем) перенесено из cog-уровневой свободной функции
  `_validate_week_range` в `DateRange.week(start, end, *, today)` — домен теперь владеет всем
  правилом целиком (включая уже существовавшую проверку `end < start` в `__post_init__`), а не
  делит его между слоями. Порядок проверок (future-check → конструирование → >31-дней-check)
  подтверждён ревью как поведенчески идентичный старому двухшаговому варианту для всех входов.
- **PRES-8**: дубликат DOM-8, уже исправлено в Этапе 1 (`domain/errors.py::PermissionError`
  удалён) — действий в этом этапе не потребовалось.
- **PRES-9** (`presentation/bot.py::StalbotBot.close()`): `close()` раньше звал `.cancel()` на
  4 фоновых `tasks.Loop` и сразу закрывал `cache_db` — `Loop.cancel()` только запрашивает отмену,
  не дожидается реального разворачивания итерации, так что закрытие `cache_db` могло гоняться с
  ещё не завершившимся запросом к ней. Подтверждено эмпирически: `sqlite3.ProgrammingError:
  Cannot operate on a closed database` в шумных логах прогона тестов (фоновый `_run_metrics_log`)
  до фикса. `close()` теперь собирает `asyncio.Task` каждой петли через `Loop.get_task()` и
  дожидается их через `asyncio.gather(..., return_exceptions=True)` перед закрытием кэша,
  обёрнуто в `asyncio.wait_for(..., timeout=_SHUTDOWN_TIMEOUT_SECONDS)` (60с, добавлено по
  итогам ревью — без таймаута зависшая задача блокировала бы shutdown навсегда).
- Обязательный `agent-skills:code-reviewer` — **APPROVE** с первого прохода; 2 important-finding
  (нет таймаута на `asyncio.gather` в `close()`; новый regression-тест PRES-9 покрывал только
  happy path, не случай, когда задача петли реально бросает исключение) — оба устранены сразу
  (таймаут с логированием + fallback на закрытие кэша; добавлен тест
  `test_close_still_closes_the_cache_when_a_loop_task_raises`). 1 suggestion (расширение
  `_disable_children` на `discord.ui.Select`, не только `Button`, — задокументировано
  однострочным комментарием как намеренное).
- Все 958 unit-тестов зелёные (+7 к концу Этапа 4, минус 6 удалённых тестов
  `PaginatedItemSelect`, итого чистый +33/-26 с учётом удаления), `mypy --strict`/`ruff` чисты
  по всему `src`+`tests`.
- Коммиты атомарные по одной находке/группе: PRES-2/PRES-3 (`79c00f1`), PRES-4 (`33c62d2`),
  PRES-5 (`788171b`), PRES-7 (`406be97`), PRES-9 (`64cecb5`).
- Новых блокирующих багов по пути не найдено.

Review passed: да (`agent-skills:code-reviewer`, five-axis, APPROVE после устранения двух
important-findings по `close()`'s таймауту и test-coverage; mypy --strict/ruff/полный прогон
тестов зелёные)

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
| 1 | Domain | 10 | 1 (DOM-1 дублирован из эт.0) | done |
| 2 | Application | 8 | 2 (APP-1/2 дублированы из эт.0) | done |
| 3 | Infrastructure: Sheets+Cache | 10 | 2 (дублированы из эт.0) | done |
| 4 | Infrastructure: Discord/OCR/Logging/Config | 9 | 0 (2 high) | done |
| 5 | Presentation (non-ticket) | 9 | 1 (PRES-1 дублирован из эт.0) | done |
| 6 | Presentation: Tickets | 10 | 1 (TICK-1 дублирован из эт.0) | not started |
| 7 | Security (остаточные) | 5 | 0 | not started |
| 8 | Test-coverage backfill | 4 | — | not started |

**Итого уникальных находок: ~57** (с учётом дублей CLUSTER-1 и DOM-8/PRES-8, DOM-2/SEC-2,
посчитанных один раз; +2 за INFRA2-10/INFRA2-11, найденные при ревью Этапа 4).

Этапы 0, 1, 2, 3, 4 и 5 исправлены и прошли ревью (2026-08-05). Этап 6 (Presentation:
Ticket flow) — следующий по очереди, ждёт явной команды на продолжение (правило "один
этап за раз").
