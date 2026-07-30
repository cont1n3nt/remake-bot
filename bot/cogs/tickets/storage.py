"""Персистентность тикетов: временное состояние формы (в памяти),
опубликованные заявки и журналы сделок (JSON на диске).

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.2)."""

import asyncio
import json
import os
from typing import Optional

DEAL_REPORTS_DIR = "deal_reports"


# ------------------------------------------------------------------ #
#  Хранилище временных данных формы (в памяти)                       #
# ------------------------------------------------------------------ #

class FormDataStore:
    def __init__(self):
        self._data: dict[int, dict] = {}

    def get(self, user_id: int) -> dict:
        return self._data.setdefault(user_id, {})

    def set(self, user_id: int, key: str, value):
        self._data.setdefault(user_id, {})[key] = value

    def clear(self, user_id: int):
        self._data.pop(user_id, None)


form_store = FormDataStore()


# ------------------------------------------------------------------ #
#  Хранилище опубликованных заявок (для редактирования)              #
# ------------------------------------------------------------------ #

REQUESTS_FILE = "published_requests.json"


def _save_request_meta_sync(channel_id: int, message_id: int, user_id: int, data: dict) -> None:
    try:
        with open(REQUESTS_FILE, encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        meta = {}
    meta[str(message_id)] = {
        "channel_id": channel_id,
        "user_id": user_id,
        "data": data,
    }
    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


async def _save_request_meta(channel_id: int, message_id: int, user_id: int, data: dict) -> None:
    await asyncio.to_thread(_save_request_meta_sync, channel_id, message_id, user_id, data)


def _load_request_meta(message_id: int) -> Optional[dict]:
    try:
        with open(REQUESTS_FILE, encoding="utf-8") as f:
            meta = json.load(f)
        return meta.get(str(message_id))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _load_request_meta_by_channel(channel_id: int) -> Optional[tuple[int, dict]]:
    """Find the published request card for a ticket channel (most recently saved one)."""
    try:
        with open(REQUESTS_FILE, encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    match_id, match_data = None, None
    for message_id_str, data in meta.items():
        if data.get("channel_id") == channel_id:
            match_id, match_data = message_id_str, data
    if match_id is None:
        return None
    return int(match_id), match_data


async def _delete_request_meta(message_id: int) -> None:
    def _sync():
        try:
            with open(REQUESTS_FILE, encoding="utf-8") as f:
                meta = json.load(f)
            meta.pop(str(message_id), None)
            with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    await asyncio.to_thread(_sync)


# ------------------------------------------------------------------ #
#  Журналы сделок/OCR по тикет-каналам                               #
# ------------------------------------------------------------------ #

async def _save_deal_report(channel_id: int, entry: dict) -> None:
    os.makedirs(DEAL_REPORTS_DIR, exist_ok=True)
    path = os.path.join(DEAL_REPORTS_DIR, f"{channel_id}.json")

    def _sync_save() -> None:
        try:
            with open(path, encoding="utf-8") as f:
                report: list[dict] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            report = []
        report.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    await asyncio.to_thread(_sync_save)
