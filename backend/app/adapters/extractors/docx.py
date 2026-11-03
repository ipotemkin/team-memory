"""DOCX (Office Open XML) via python-docx: paragraphs and table cells in document order.

Paragraphs are separated by blank lines (so chunking keeps them apart); a heading is glued to the
next paragraph like in the .txt/.md knowledge-base documents; a table becomes one block, one line
per row, cells joined with " | " (merged cells are not repeated).
"""

import io
import zipfile

from docx import Document as open_docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.adapters.extractors.base import FileFormat, tidy
from app.domain.errors import ExtractionFailed


def _is_heading(p: Paragraph) -> bool:
    name = (p.style.name if p.style is not None else "") or ""
    return name.lower().startswith(("heading", "title", "заголовок"))


def _table_text(t: Table) -> str:
    rows: list[str] = []
    for row in t.rows:
        cells: list[str] = []
        for cell in row.cells:
            txt = " ".join(cell.text.split())
            if txt and (not cells or cells[-1] != txt):  # merged cells repeat in python-docx
                cells.append(txt)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


class DocxExtractor(FileFormat):
    format = "docx"
    mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    extensions = frozenset({".docx"})
    content_types = frozenset({mime_type, "application/zip", "application/x-zip-compressed"})
    magic = (b"PK\x03\x04",)

    def extract(self, data: bytes) -> str:
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
