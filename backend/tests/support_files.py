"""Образцы файлов для тестов экстракторов и API (docx собирается в тесте, pdf — закоммиченная фикстура)."""

import io
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("Отпускные правила", level=1)
    doc.add_paragraph("Заявку на отгул подают за три рабочих дня через портал HR.")
    doc.add_paragraph("")  # empty paragraphs are skipped
    doc.add_paragraph("Ёлка в офисе ставится 20 декабря.")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Тип", "Срок"
    t.cell(1, 0).text, t.cell(1, 1).text = "Отгул", "3 дня"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def sample_pdf() -> bytes:
    """Two-page Cyrillic PDF with a text layer (scripts/make_samples.py, fpdf2 + DejaVu Sans)."""
    return (FIXTURES / "oncall.pdf").read_bytes()


def blank_pdf() -> bytes:
    """Valid PDF without any text layer (like a scan without OCR)."""
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()
