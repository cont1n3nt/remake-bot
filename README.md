# stalbot

Discord-бот для Stalcraft (Stalzone).

Архитектура и план реализации — см. [`PLAN.md`](PLAN.md).
Статус выполнения — см. [`PLAN_PROGRESS.md`](PLAN_PROGRESS.md).

---

## Требования

- Python **3.12+**
- Discord-приложение (бот) с включёнными privileged intents
- (опционально) Docker 24+ / Docker Compose v2, либо `systemd` для деплоя как сервиса

---

## 1. Установка

```bash
git clone <repo-url> stalbot
cd stalbot
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Расширение `[dev]` ставит `ruff`, `mypy`, `pytest`, `pytest-cov`, `hypothesis`, `pre-commit` —
всё, что нужно для разработки и CI. Для продакшена без разработческих зависимостей:

```bash
pip install -e .
```

OCR-движки (задел под M13, не нужны для v1.0) — отдельный extra:

```bash
pip install -e ".[ocr]"
```

---

## 2. Discord-приложение

1. Создайте приложение в [Discord Developer Portal](https://discord.com/developers/applications) →
   вкладка **Bot** → **Reset Token** → сохраните токен (он показывается один раз).
2. На вкладке **Bot** включите **Privileged Gateway Intents**:
   - `SERVER MEMBERS INTENT`
   - `MESSAGE CONTENT INTENT`

   (третий нужный интент, `guild_messages`, не привилегированный и включён по умолчанию).
3. Пригласите бота на сервер (OAuth2 → URL Generator, scope `bot` + `applications.commands`)
   с правами:
   - `Manage Roles` (роль бота должна стоять **выше** всех ролей рангов/рефералов, которые он выдаёт)
   - `View Channels`
   - `Send Messages`
   - `Embed Links`
   - `Attach Files`
   - `Read Message History`
4. На сервере убедитесь, что роль бота в списке ролей находится выше `RANK_ROLE_IDS` и
   `REFERRAL_ROLE_IDS` (см. `src/stalbot/config/ids.py`) — иначе выдача роли будет молча
   игнорироваться Discord API.

---

## 3. Конфигурация

Скопируйте шаблон и заполните значениями:

```bash
cp .env.example .env
```

Обязательные переменные (бот не запустится без них — `pydantic-settings` валидирует `.env`
при старте, до подключения к Discord):

| Переменная | Назначение |
|---|---|
| `DISCORD_TOKEN` | токен бота из Developer Portal |
| `GUILD_ID` | ID сервера |
| `LOG_CHANNEL_ID` | канал аудита/ошибок |
| `REVIEWS_CHANNEL_ID` | канал с напоминанием об отзыве после сделки |

Остальные переменные — с рабочими значениями по умолчанию (поведенческие флаги, пути к
данным, OCR — см. комментарии в `.env.example` и `src/stalbot/config/settings.py`).

---

## 4. Первый запуск

```bash
python -m stalbot
# или, после `pip install`, консольный скрипт:
stalbot
```

При старте бот:

1. Валидирует `.env` (fail-fast при отсутствии обязательных переменных).
2. Открывает локальный SQLite-кэш (`data/cache.sqlite3` по умолчанию — файл и директория
   создаются автоматически) и применяет накопившиеся миграции схемы.
3. Регистрирует slash-команды (`tree.sync()`) и persistent views.
4. Подключается к Discord.

Проверить, что всё поднялось: команда `/healthcheck` (админ-only) показывает состояние кэша,
целостность базы (`PRAGMA quick_check`) и uptime.

---

## 5. Запуск в Docker

```bash
docker compose up -d --build
```

`docker-compose.yml` монтирует `./data` как volume наружу контейнера и читает `.env` из корня
проекта — `.env` должен существовать на хосте до старта (см. раздел 3 выше). Контейнер работает
от непривилегированного пользователя (см. `Dockerfile`).

Логи: `docker compose logs -f stalbot`. Остановка: `docker compose down`.

---

## 6. Запуск как systemd-сервис (альтернатива Docker)

```bash
sudo cp deploy/stalbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stalbot
```

Перед этим отредактируйте `deploy/stalbot.service`: путь к проекту (`WorkingDirectory`), путь
к интерпретатору венва (`ExecStart`) и системного пользователя (`User`/`Group`), от имени
которого будет работать бот (не `root`). Подробности — в комментариях самого юнита.

```bash
systemctl status stalbot
journalctl -u stalbot -f
```

---

## 7. Бэкапы

SQLite — единственный источник истины (sqlite_migration.md): потерянная база — это потерянные
данные, а не «пересоберётся следующим синком».

```bash
RCLONE_REMOTE=b2:stalbot-backups ./scripts/backup.sh
```

Снимает снапшот `data/cache.sqlite3` в `backups/<timestamp>/` в двух форматах — бинарный
`cache.sqlite3.backup` (SQLite online backup API, без остановки бота) и логический
`cache.sqlite3.dump.gz` (`.dump | gzip`: если бинарная копия повреждена на уровне страниц,
текстовый SQL-дамп можно починить руками). После снятия копии `PRAGMA integrity_check`
прогоняется **на самой копии**, не на источнике — иначе проверка доказывает только то, что жив
оригинал.

Ретенция дед-отец-сын: последние `KEEP_DAYS` (по умолчанию 7) — ежедневно, следующие
`KEEP_WEEKS` (4) — по одной в неделю, следующие `KEEP_MONTHS` (12) — по одной в месяц.

**Вынос за пределы VPS обязателен** — при отказе диска или потере доступа к самому VPS локальные
бэкапы пропадают вместе с базой. `backup.sh` требует `rclone` на `PATH` и `RCLONE_REMOTE`
(результат `rclone remotes`, например `b2:stalbot-backups`) и **завершается ошибкой**, если это
не настроено — обойти можно только явным `BACKUP_SKIP_REMOTE=1`, и только для локальной
разработки, никогда в проде. После загрузки скрипт сверяет копию на удалённом хранилище с
локальной (`rclone check`), а не просто проверяет, что `rclone copy` не упал.

Ставится по расписанию через `deploy/stalbot-backup.{service,timer}` (systemd) — см. заголовок
`.service`-файла для установки. `RCLONE_REMOTE` кладите в отдельный `/opt/stalbot/.env.backup`
(не в основной `.env` бота) — это операционный секрет с доступом к бэкап-хранилищу, а не
конфигурация самого бота.

### Восстановление

```bash
./scripts/restore.sh backups/<timestamp>
```

По умолчанию — учебная тревога, не боевая замена: восстанавливает выбранный снимок во временный
каталог, гоняет `PRAGMA integrity_check`, печатает количество строк в каждой таблице для ручной
сверки — и ничего не трогает в проде. Чтобы реально заменить рабочую базу (сам скрипт останавливает
`stalbot.service`, если он запущен, и сохраняет текущий файл перед перезаписью):

```bash
./scripts/restore.sh backups/<timestamp> --apply /opt/stalbot/data/cache.sqlite3
```

Учебную тревогу стоит проводить не реже раза в квартал — бэкап, который никогда не пробовали
восстановить, не бэкап, а предположение.

---

## 8. Разработка

```bash
ruff check .
ruff format --check .
mypy --strict
pytest --cov
```

`pre-commit` хуки (`.pre-commit-config.yaml`) гоняют `ruff`/`mypy` перед каждым коммитом:

```bash
pre-commit install
```

CI (`.github/workflows/ci.yml`) прогоняет тот же набор проверок на каждый push/PR;
порог покрытия — 85 % (`pyproject.toml::[tool.coverage.report]`).

---

## Известные ограничения v1.0

- OCR распознавания скриншотов нет — сумма сделки вводится администратором вручную
  (`NullOcrGateway`). Вся инфраструктура под OCR уже собирает датасет для будущего этапа M13
  (см. `PLAN_PROGRESS.md`).
