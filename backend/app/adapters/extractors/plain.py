"""Текстовые форматы (.txt, .md): UTF-8 (BOM допускается), текст сохраняется как есть."""

from app.adapters.extractors.base import FileFormat
from app.domain.errors import UnsupportedFile


class PlainTextExtractor(FileFormat):
    """Адаптер .txt (без сигнатуры: любой UTF-8 текст)."""

    format = "txt"
    mime_type = "text/plain"
    extensions = frozenset({".txt"})
    content_types = frozenset({"text/plain"})

    def extract(self, data: bytes) -> str:
        """Декодирует UTF-8, отбрасывая BOM.

        Raises:
            UnsupportedFile: не UTF-8 (например, cp1251) — 415, как и раньше для текстовых загрузок.
        """
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UnsupportedFile("text must be UTF-8") from None


class MarkdownExtractor(PlainTextExtractor):
    """Адаптер .md/.markdown; принимает и text/plain, т.к. многие клиенты не знают тип markdown."""

    format = "md"
    mime_type = "text/markdown"
    extensions = frozenset({".md", ".markdown"})
    content_types = frozenset({"text/markdown", "text/x-markdown", "text/plain"})
