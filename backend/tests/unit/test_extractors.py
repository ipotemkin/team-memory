"""TextExtractor adapters and TextExtractionService (format resolution, magic bytes, errors)."""

import pytest

from app.adapters.extractors import (
    DocxExtractor,
    MarkdownExtractor,
    PdfExtractor,
    PlainTextExtractor,
    build_extractors,
)
from app.domain.errors import ExtractionFailed, UnsupportedFile
from app.services.extraction import TextExtractionService
from tests.support_files import DOCX_MIME, blank_pdf, make_docx, sample_pdf


def svc(formats: str = "txt,md,docx,pdf") -> TextExtractionService:
    return TextExtractionService(build_extractors(formats.split(",")))


def test_plain_text_utf8_bom_and_verbatim() -> None:
    assert PlainTextExtractor().extract("\ufeffПривет,  мир\r\n".encode()) == "Привет,  мир\r\n"
    with pytest.raises(UnsupportedFile, match="UTF-8"):
        PlainTextExtractor().extract("привет".encode("cp1251"))


def test_docx_paragraphs_headings_and_tables() -> None:
    text = DocxExtractor().extract(make_docx())
    assert text == (
        "Отпускные правила\nЗаявку на отгул подают за три рабочих дня через портал HR.\n\n"
        "Ёлка в офисе ставится 20 декабря.\n\n"
        "Тип | Срок\nОтгул | 3 дня"
    )


def test_docx_corrupt_zip_is_extraction_error() -> None:
    with pytest.raises(ExtractionFailed, match="DOCX"):
        DocxExtractor().extract(b"PK\x03\x04 definitely not a docx")


def test_pdf_cyrillic_text_layer_and_page_break() -> None:
    text = PdfExtractor().extract(sample_pdf())
    assert text.startswith("Регламент дежурств\nКто дежурит\n")
    assert "Если дежурный не ответил за 15 минут, алерт эскалируется тимлиду." in text
    # page 2 starts after a blank line -> its own chunk
    assert "тимлиду.\n\nПередача смены\n" in text
    assert "отчёт" in text  # ё survives


def test_pdf_without_text_layer() -> None:
    with pytest.raises(ExtractionFailed, match="no text layer"):
        PdfExtractor().extract(blank_pdf())


def test_pdf_corrupt() -> None:
    with pytest.raises(ExtractionFailed, match="PDF"):
        PdfExtractor().extract(b"%PDF-1.7\n garbage without xref")


@pytest.mark.parametrize(
    ("filename", "ctype", "fmt"),
    [
        ("a.txt", "text/plain", "txt"),
        ("a.md", "text/markdown", "md"),
        ("a.md", "text/plain", "md"),
        ("notes", "text/plain", "txt"),  # no extension: by declared type
        ("Отчёт.DOCX", DOCX_MIME, "docx"),
        ("a.docx", "application/octet-stream", "docx"),
        ("a.pdf", "application/pdf", "pdf"),
        ("a.pdf", "", "pdf"),
    ],
)
def test_resolve(filename: str, ctype: str, fmt: str) -> None:
    assert svc().resolve(filename, ctype).format == fmt


@pytest.mark.parametrize(
    ("filename", "ctype"),
    [
        ("a.exe", "application/octet-stream"),
        ("a.pdf", "text/plain"),
        ("a.docx", "application/pdf"),
        ("x", ""),
    ],
)
def test_resolve_rejects(filename: str, ctype: str) -> None:
    with pytest.raises(UnsupportedFile, match="supported formats"):
        svc().resolve(filename, ctype)


async def test_magic_bytes_mismatch() -> None:
    with pytest.raises(UnsupportedFile, match="does not look like PDF"):
        await svc().extract("a.pdf", "application/pdf", b"PK\x03\x04 zip, not pdf")
    with pytest.raises(UnsupportedFile, match="does not look like DOCX"):
        await svc().extract("a.docx", DOCX_MIME, sample_pdf())


async def test_service_extract_and_formats_config() -> None:
    res = await svc().extract("a.pdf", "application/pdf", sample_pdf())
    assert (res.format, res.mime_type) == ("pdf", "application/pdf") and "дежурный" in res.text
    only_text = svc("txt, md".replace(" ", ""))
    assert only_text.formats == ["txt", "md"]
    with pytest.raises(UnsupportedFile):
        await only_text.extract("a.pdf", "application/pdf", sample_pdf())
    with pytest.raises(ValueError, match="unknown format"):
        build_extractors(["txt", "rtf"])


async def test_unexpected_parser_error_becomes_422() -> None:
    class Broken(MarkdownExtractor):
        def extract(self, data: bytes) -> str:
            raise RuntimeError("bug")

    with pytest.raises(ExtractionFailed, match="cannot read MD"):
        await TextExtractionService([Broken()]).extract("a.md", "text/markdown", b"x")
