"""Адаптеры порта TextExtractor: обычный текст (txt/md), docx (python-docx), pdf (pypdf).

Новый формат: адаптер-наследник ``FileFormat`` + добавить его в ``ALL_EXTRACTORS``; включается
перечислением в UPLOAD_FORMATS. Библиотеки форматов импортируются только здесь (import-linter).
"""

from app.adapters.extractors.base import FileFormat
from app.adapters.extractors.docx import DocxExtractor
from app.adapters.extractors.pdf import PdfExtractor
from app.adapters.extractors.plain import MarkdownExtractor, PlainTextExtractor

# Все известные форматы (реестр адаптеров).
ALL_EXTRACTORS = (PlainTextExtractor, MarkdownExtractor, DocxExtractor, PdfExtractor)


def build_extractors(formats: list[str]) -> list[FileFormat]:
    """Создаёт адаптеры для форматов из UPLOAD_FORMATS (порядок = порядок выбора; дубли убираются).

    Raises:
        ValueError: неизвестный формат — приложение не стартует с опечаткой в настройке.
    """
    known = {cls.format: cls for cls in ALL_EXTRACTORS}
    unknown = [f for f in formats if f not in known]
    if unknown:
        raise ValueError(f"UPLOAD_FORMATS: unknown format(s) {unknown}; known: {sorted(known)}")
    return [known[f]() for f in dict.fromkeys(formats)]


__all__ = [
    "ALL_EXTRACTORS",
    "DocxExtractor",
    "MarkdownExtractor",
    "PdfExtractor",
    "PlainTextExtractor",
    "build_extractors",
]
