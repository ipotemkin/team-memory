"""Загрузка DOCX / PDF сквозь систему (memory + stub): извлечение → оригинал в хранилище → индексация →
ответ.
"""

from collections.abc import Callable

import pytest

from tests.api.conftest import Api
from tests.support_files import DOCX_MIME, blank_pdf, make_docx, sample_pdf


async def _upload(api: Api, name: str, data: bytes, ctype: str) -> dict[str, object]:
    r = await api.client.post("/kb/documents", files={"file": (name, data, ctype)})
    assert r.status_code == 200, r.text
    doc: dict[str, object] = (await api.client.get(f"/kb/documents/{r.json()['document_id']}")).json()
    return doc


async def test_pdf_upload_then_answer_with_quote(api: Api) -> None:
    data = sample_pdf()
    doc = await _upload(api, "oncall.pdf", data, "application/pdf")
    assert doc["status"] == "ready" and doc["title"] == "oncall"
    assert doc["mime_type"] == "application/pdf" and doc["size_bytes"] == len(data) and doc["download_url"]
    assert "алерт эскалируется тимлиду" in str(doc["text"]) and int(str(doc["snippets_count"])) >= 2

    r = await api.client.post(
        "/kb/ask", json={"question": "Кому эскалируется алерт, если дежурный не ответил?"}
    )
    body = r.json()
    assert r.status_code == 200 and body["needs_review"] is False, body
    assert body["sources"][0]["document_id"] == doc["id"]
    assert "эскалируется тимлиду" in body["sources"][0]["quote"]
    assert api.audit[0]["input"]["format"] == "pdf"  # type: ignore[index]


async def test_docx_upload_then_answer_with_quote(api: Api) -> None:
    doc = await _upload(api, "hr.docx", make_docx(), DOCX_MIME)
    assert doc["status"] == "ready" and doc["mime_type"] == DOCX_MIME
    assert str(doc["text"]).endswith("Тип | Срок\nОтгул | 3 дня")
    r = await api.client.post("/kb/ask", json={"question": "Когда ставится ёлка в офисе?"})
    body = r.json()
    assert body["needs_review"] is False and body["sources"][0]["document_id"] == doc["id"], body
    assert "20 декабря" in body["sources"][0]["quote"]


@pytest.mark.parametrize(
    ("name", "data", "ctype", "status", "error"),
    [
        ("scan.pdf", blank_pdf(), "application/pdf", 422, "extraction_failed"),
        ("fake.pdf", b"PK\x03\x04 a zip", "application/pdf", 415, "unsupported_media_type"),
        ("fake.docx", b"%PDF-1.4 a pdf", DOCX_MIME, 415, "unsupported_media_type"),
        ("broken.docx", b"PK\x03\x04 not a docx", DOCX_MIME, 422, "extraction_failed"),
        ("a.rtf", b"{\\rtf1 hi}", "application/rtf", 415, "unsupported_media_type"),
        ("a.pdf", sample_pdf(), "text/plain", 415, "unsupported_media_type"),
    ],
)
async def test_upload_rejected(api: Api, name: str, data: bytes, ctype: str, status: int, error: str) -> None:
    r = await api.client.post("/kb/documents", files={"file": (name, data, ctype)})
    assert r.status_code == status and r.json()["error"] == error, r.text
    assert api.audit[-1]["status"] == "client_error"
    assert (await api.client.get("/kb/documents")).json() == []


async def test_scanned_pdf_message(api: Api) -> None:
    r = await api.client.post("/kb/documents", files={"file": ("scan.pdf", blank_pdf(), "application/pdf")})
    assert "no text layer" in r.json()["detail"]


async def test_upload_formats_env_disables_pdf(make_api: Callable[..., Api]) -> None:
    api = make_api(UPLOAD_FORMATS="txt,md,docx")
    r = await api.client.post("/kb/documents", files={"file": ("a.pdf", sample_pdf(), "application/pdf")})
    assert r.status_code == 415 and "txt, md, docx" in r.json()["detail"]
