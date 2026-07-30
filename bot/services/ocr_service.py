"""Сервис для OCR-распознавания скриншотов и сверки с базой цен."""

import asyncio
import json
import logging
import os
import re
from typing import Optional

from bot.utils.formatting import format_amount

logger = logging.getLogger("bot")


def _fmt(n: float) -> str:
    """Обёртка над единым форматтером (bot/utils/formatting.py).

    Оставлена ради множества существующих импортов `from ... import _fmt`."""
    return format_amount(n)


class OcrService:
    """Извлечение текста из изображений и парсинг предметов/цен."""

    def __init__(self, prices_path: str = "prices.json") -> None:
        self._reader: Optional[object] = None
        self._prices_path = prices_path
        self._prices: dict[str, float] = {}
        self._load_prices()

    # ------------------------------------------------------------------ #
    #  База цен                                                          #
    # ------------------------------------------------------------------ #

    def _load_prices(self) -> None:
        """Загрузка цен из JSON-файла. Если файла нет — логируем предупреждение."""
        if not os.path.exists(self._prices_path):
            logger.warning("Файл цен %s не найден", self._prices_path)
            self._prices = {}
            return
        try:
            with open(self._prices_path, encoding="utf-8") as f:
                self._prices = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Ошибка загрузки %s: %s", self._prices_path, e)
            self._prices = {}

    async def reload_prices(self) -> None:
        """Перезагрузка цен из файла (async-обёртка)."""
        await asyncio.to_thread(self._load_prices)

    # ------------------------------------------------------------------ #
    #  OCR — распознавание текста                                        #
    # ------------------------------------------------------------------ #

    def _get_reader(self):
        """Ленивая инициализация easyocr.Reader."""
        if self._reader is None:
            import easyocr
            self._reader = easyocr.Reader(["ru", "en"], gpu=False)
        return self._reader

    async def extract_text(self, image_bytes: bytes) -> str:
        """Извлечение текста из изображения (байты → строка).

        Raises:
            ValueError: если изображение не удалось декодировать.
            asyncio.TimeoutError: если OCR превысил 30 секунд.
        """
        import numpy as np
        from PIL import Image
        import io

        # Декодируем байты в numpy-массив для easyocr
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img_array = np.array(img)
        except Exception as exc:
            raise ValueError(f"Не удалось декодировать изображение: {exc}") from exc

        reader = self._get_reader()
        results = await asyncio.to_thread(reader.readtext, img_array)

        # Отбираем блоки с уверенностью >= 30 %
        texts = [text for _, text, conf in results if conf >= 0.3]
        return "\n".join(texts)

    # ------------------------------------------------------------------ #
    #  Парсинг предметов из текста                                       #
    # ------------------------------------------------------------------ #

    def parse_items(self, text: str) -> list[dict]:
        """Извлечение списка предметов и их количества из распознанного текста.

        Поддерживаются форматы:
          - "Название x5" / "Название х5"
          - "5x Название"
          - "Название 5шт"
        Если количество не указано — считается 1.
        """
        items: list[dict] = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Пробуем: "Название x5" или "Название х5" (русская/латинская x)
            m = re.search(r'(.+?)\s*[xхХ×]\s*(\d[\d\s]*)', line)
            if m:
                name = m.group(1).strip()
                qty_str = re.sub(r"\s+", "", m.group(2))
                try:
                    qty = int(qty_str)
                except ValueError:
                    qty = 1
            else:
                # Пробуем: "Название 5шт"
                m = re.search(r'(.+?)\s+(\d+)\s*шт', line)
                if m:
                    name = m.group(1).strip()
                    qty = int(m.group(2))
                else:
                    name = line
                    qty = 1

            if name:
                items.append({"name": name, "quantity": qty})

        return items

    # ------------------------------------------------------------------ #
    #  Сверка с ценами                                                   #
    # ------------------------------------------------------------------ #

    def cross_reference(self, parsed: list[dict]) -> tuple[list[str], float, list[str]]:
        """Сверка каждого предмета с базой цен.

        Возвращает:
            - readable:  строки вида "Название x5 = 1000 ₽"
            - total:     общая сумма
            - unknown:   список ненайденных названий
        """
        readable: list[str] = []
        total = 0.0
        unknown: list[str] = []

        for item in parsed:
            name = item["name"]
            qty = item["quantity"]

            # Регистронезависимый поиск в базе
            price: Optional[float] = None
            for key, val in self._prices.items():
                if key.lower() == name.lower():
                    price = val
                    break

            if price is not None:
                line_total = price * qty
                total += line_total
                readable.append(f"{name} x{qty} = {_fmt(line_total)} ₽")
            else:
                unknown.append(name)
                readable.append(f"{name} x{qty} = ❓ UNKNOWN")

        return readable, total, unknown
