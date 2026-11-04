"""TextExtractionService — выбор адаптера TextExtractor для загруженного файла и извлечение текста.

Проверки по порядку: формат разрешён (UPLOAD_FORMATS) по расширению / заявленному MIME →
сигнатура (magic bytes) → извлечение. Неподдерживаемый или не совпадающий файл → UnsupportedFile
(415); нечитаемый / без текста → ExtractionFailed (422). Разбор выполняется в отдельном потоке
(``asyncio.to_thread``), т.к. парсинг DOCX/PDF нагружает CPU и блокировал бы event loop.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.domain.errors import ExtractionFailed, UnsupportedFile
from app.domain.ports import TextExtractor

log = logging.getLogger("app.extraction")


@dataclass(frozen=True)
class ExtractedText:
    """Результат извлечения: текст, канонический MIME-тип и имя формата (для аудита)."""

    text: str
    mime_type: str
    format: str


def extension_of(filename: str) -> str:
    """Расширение файла в нижнем регистре с точкой (".pdf") или ""."""
    return PurePosixPath(filename or "").suffix.lower()


class TextExtractionService:
    """Сервис извлечения текста.

    Args:
        extractors: разрешённые адаптеры (собираются в composition.py по UPLOAD_FORMATS); порядок важен —
            выбирается первый подходящий.
    """

    def __init__(self, extractors: Sequence[TextExtractor]) -> None:
        self._extractors = list(extractors)

    @property
    def formats(self) -> list[str]:
        """Имена разрешённых форматов (для сообщений об ошибке)."""
        return [e.format for e in self._extractors]

    def resolve(self, filename: str, content_type: str | None) -> TextExtractor:
        """Находит адаптер по расширению имени файла и MIME-типу.

        Raises:
            UnsupportedFile: ни один разрешённый адаптер не подходит.
        """
        ext = extension_of(filename)
        # Отбрасываем параметры MIME ("; charset=...") и регистр.
        ct = (content_type or "").split(";")[0].strip().lower()
        for e in self._extractors:
            if e.supports(ext, ct):
                return e
        raise UnsupportedFile(f"supported formats: {', '.join(self.formats)} (got {ext or ct or 'unknown'})")

    async def extract(self, filename: str, content_type: str | None, data: bytes) -> ExtractedText:
        """Проверяет файл и извлекает из него текст.

        Args:
            filename: исходное имя файла.
            content_type: заявленный MIME-тип (может быть None или с параметрами, например ``;
            charset=utf-8``).
            data: содержимое файла.

        Raises:
            UnsupportedFile: формат не разрешён или сигнатура не совпадает (например, «PDF», который на
                самом деле ZIP).
            ExtractionFailed: файл не читается или в нём нет текста.
        """
        ex = self.resolve(filename, content_type)
        # Проверка magic bytes: расширению и MIME клиента не доверяем, смотрим на первые байты.
        if not ex.matches(data):
            raise UnsupportedFile(f"file content does not look like {ex.format.upper()}")
        try:
            text = await asyncio.to_thread(ex.extract, data)
        except (UnsupportedFile, ExtractionFailed):
            raise
        except Exception as e:  # noqa: BLE001 - баг парсера/экзотичный файл:
            # понятный 422 вместо 500
            log.warning("extraction of %s failed: %r", filename, e)
            raise ExtractionFailed(f"cannot read {ex.format.upper()}: {type(e).__name__}") from None
        return ExtractedText(text=text, mime_type=ex.mime_type, format=ex.format)
