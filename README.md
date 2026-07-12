# Discord Trade Bot

Discord-бот для фиксации сделок пользователей с записью в Google Sheets.

## Stack

- Python 3.12+
- discord.py 2.x
- Google Sheets API (gspread)
- Service Account авторизация

## Запуск

```bash
cp .env.example .env
# заполните .env
pip install -e .
bot
```

## Команды

- `/profile` — профиль пользователя
- `/ref <code>` — установка реферального кода
- `/referrals` — статистика рефералов
- `/add` — фиксация сделки (admin only)
