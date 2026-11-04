"""Общая логика адаптеров TextExtractor: выбор по расширению + заявленному MIME + сигнатуре (magic bytes)."""

import re
from typing import ClassVar

# Браузеры/curl часто шлют такие типы для любого бинарного/неизвестного файла —
# тогда полагаемся на расширение и magic bytes.
GENERIC_CONTENT_TYPES = frozenset({"", "application/octet-stream"})
_TRAILING_WS = re.compile(r"[ \t\u00a0]+\n")
_MANY_BLANKS = re.compile(r"\n{3,}")


class FileFormat:
    """Базовый класс адаптера формата: описание формата в атрибутах класса.

    Подклассу достаточно задать ``format``, ``mime_type``, ``extensions``, ``content_types``, ``magic`` и
    реализовать ``extract``.
    """

    format: ClassVar[str]
    mime_type: ClassVar[str]
    extensions: ClassVar[frozenset[str]]
    content_types: ClassVar[frozenset[str]]  # допустимые заявленные типы (кроме общих)
    magic: ClassVar[tuple[bytes, ...]] = ()  # пусто → без проверки сигнатуры

    def supports(self, extension: str, content_type: str) -> bool:
        """Подходит ли формат.

        С расширением: расширение наше И MIME либо наш, либо «общий» (браузер не знает тип). Без
        расширения
        решаем только по MIME.
        """
        if extension:
            return extension in self.extensions and (
                content_type in self.content_types or content_type in GENERIC_CONTENT_TYPES
            )
        return content_type in self.content_types

    def matches(self, data: bytes) -> bool:
        """Содержимое начинается с одной из сигнатур формата (пустой ``magic`` — проверки нет)."""
        return not self.magic or any(data.startswith(m) for m in self.magic)

    def extract(self, data: bytes) -> str:  # pragma: no cover - абстрактный
        raise NotImplementedError


def tidy(text: str) -> str:
    """Нормализует текст: переводы строк → ``\n``, без пробелов в конце строк и без NUL,
    не больше одной пустой строки между абзацами (пустая строка — граница фрагмента при нарезке).
    """
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    t = _TRAILING_WS.sub("\n", t + "\n")
    return _MANY_BLANKS.sub("\n\n", t).strip()
