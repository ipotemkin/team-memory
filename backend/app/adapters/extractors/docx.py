"""DOCX (Office Open XML) через python-docx: абзацы и ячейки таблиц в порядке документа.

Абзацы разделяются пустыми строками (чтобы нарезка держала их отдельно); заголовок «приклеивается»
к следующему абзацу, как в .txt/.md-документах базы знаний; таблица становится одним блоком — строка
на строку таблицы, ячейки через " | " (объединённые ячейки не повторяются).
"""

import io
import zipfile

from docx import Document as open_docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.adapters.extractors.base import FileFormat, tidy
from app.domain.errors import ExtractionFailed


def _is_heading(p: Paragraph) -> bool:
    """Абзац со стилем заголовка (англ. или рус. название стиля)."""
    name = (p.style.name if p.style is not None else "") or ""
    return name.lower().startswith(("heading", "title", "заголовок"))


def _table_text(t: Table) -> str:
    """Таблица → текст: строка таблицы на строку, ячейки через " | "."""
    rows: list[str] = []
    for row in t.rows:
        cells: list[str] = []
        for cell in row.cells:
            txt = " ".join(cell.text.split())
            if txt and (not cells or cells[-1] != txt):  # python-docx повторяет объединённые ячейки
                cells.append(txt)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


class DocxExtractor(FileFormat):
    """Адаптер DOCX. Сигнатура ``PK\x03\x04`` — DOCX это ZIP-архив."""

    format = "docx"
    mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    extensions = frozenset({".docx"})
    content_types = frozenset({mime_type, "application/zip", "application/x-zip-compressed"})
    magic = (b"PK\x03\x04",)

    def extract(self, data: bytes) -> str:
        """Текст документа.

        Raises:
            ExtractionFailed: не ZIP/не DOCX или в документе нет текста.
        """
        try:
            doc = open_docx(io.BytesIO(data))
        except (zipfile.BadZipFile, KeyError, ValueError) as e:
            raise ExtractionFailed(f"cannot read DOCX: {type(e).__name__}") from None
        blocks: list[str] = []
        heading: str | None = None
        for item in doc.iter_inner_content():
            text = _table_text(item) if isinstance(item, Table) else item.text.strip()
            if not text:
                continue
            # Заголовок откладываем до следующего блока; два заголовка подряд — первый уходит отдельно.
            if isinstance(item, Paragraph) and _is_heading(item):
                if heading is not None:
                    blocks.append(heading)
                heading = text
                continue
            blocks.append(f"{heading}\n{text}" if heading is not None else text)
            heading = None
        if heading is not None:
            blocks.append(heading)
        text = tidy("\n\n".join(blocks))
        if not text:
            raise ExtractionFailed("DOCX contains no text")
        return text
