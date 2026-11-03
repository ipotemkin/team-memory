"""TextExtractor adapters: plain text (txt/md), docx (python-docx), pdf (pypdf)."""

from app.adapters.extractors.base import FileFormat
from app.adapters.extractors.docx import DocxExtractor
from app.adapters.extractors.pdf import PdfExtractor
from app.adapters.extractors.plain import MarkdownExtractor, PlainTextExtractor

ALL_EXTRACTORS = (PlainTextExtractor, MarkdownExtractor, DocxExtractor, PdfExtractor)


def build_extractors(formats: list[str]) -> list[FileFormat]:
    """Instantiate the extractors enabled in UPLOAD_FORMATS (order = resolution order)."""
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
