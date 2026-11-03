#!/usr/bin/env python3
"""Regenerate the sample .docx/.pdf (tests_data/samples and backend/tests/fixtures).

    cd backend && uv run python ../scripts/make_samples.py [--font /path/DejaVuSans.ttf]

Needs python-docx (runtime dep) and fpdf2 (dev dep). The PDF embeds a subset of a Unicode TTF
(DejaVu Sans by default) so the text layer has proper Cyrillic (ToUnicode map).
"""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Регламент дежурств"
SECTIONS = [
    (
        "Кто дежурит",
        [
            "Дежурный инженер назначается на неделю, смена начинается в понедельник в 10:00.",
            "График дежурств публикуется в канале #oncall не позднее пятницы.",
        ],
    ),
    (
        "Реакция на инциденты",
        [
            "На инцидент P1 дежурный отвечает в течение 15 минут, на P2 — в течение одного часа.",
            "Если дежурный не ответил за 15 минут, алерт эскалируется тимлиду.",
        ],
    ),
    (
        "Передача смены",
        [
            "При передаче смены дежурный пишет короткий отчёт: открытые инциденты и риски.",
        ],
    ),
]
TABLE = [("Приоритет", "Время реакции"), ("P1", "15 минут"), ("P2", "1 час"), ("P3", "1 рабочий день")]


def make_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading(TITLE, level=1)
    for head, paras in SECTIONS:
        doc.add_heading(head, level=2)
        for p in paras:
            doc.add_paragraph(p)
    table = doc.add_table(rows=len(TABLE), cols=2)
    for r, row in enumerate(TABLE):
        for c, val in enumerate(row):
            table.cell(r, c).text = val
    doc.core_properties.created = datetime(2026, 1, 1, tzinfo=UTC)
    doc.save(str(path))


def make_pdf(path: Path, font: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))  # reproducible bytes
    pdf.add_font("DejaVu", "", str(font))
    pdf.set_font("DejaVu", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 8, TITLE, new_x="LMARGIN", new_y="NEXT")
    for i, (head, paras) in enumerate(SECTIONS):
        if i == 2:
            pdf.add_page()  # second page: page break must survive extraction
        pdf.ln(4)
        pdf.multi_cell(0, 8, head, new_x="LMARGIN", new_y="NEXT")
        for p in paras:
            pdf.multi_cell(0, 8, p, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", default="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    a = ap.parse_args()
    out = ROOT / "tests_data" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    make_docx(out / "oncall.docx")
    make_pdf(out / "oncall.pdf", Path(a.font))
    fx = ROOT / "backend" / "tests" / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    for name in ("oncall.docx", "oncall.pdf"):
        shutil.copy(out / name, fx / name)
        print(out / name, (out / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
