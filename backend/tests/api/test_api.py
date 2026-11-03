"""HTTP API on the memory backend: every endpoint, error bodies, spec scenarios, audit rows."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.domain.models import INSUFFICIENT_DATA, DocumentStatus
from tests.api.conftest import Api


async def seed(c: httpx.AsyncClient, docs: list[dict[str, Any]]) -> list[int]:
    ids = []
    for d in docs:
        r = await c.post("/kb/documents", json={"title": d["title"], "text": d["text"]})
        assert r.status_code == 200, r.text
        ids.append(r.json()["document_id"])
    return ids


# --- spec scenario 1: add document -> visible in list -------------------------------------------


async def test_add_document_then_list(api: Api) -> None:
    r = await api.client.post("/kb/documents", json={"title": "Правила", "text": "Стендап в 11:00."})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and isinstance(body["document_id"], str)
    assert body["document_id"].isdigit() and body["document_status"] == "ready"  # inline queue
    lst = (await api.client.get("/kb/documents")).json()
    assert [(d["id"], d["title"], d["status"]) for d in lst] == [(body["document_id"], "Правила", "ready")]
    assert set(lst[0]) == {"id", "title", "created_at", "status", "status_error"}
    assert lst[0]["status_error"] is None
    assert api.audit[-1]["action"] == "POST /kb/documents" and api.audit[-1]["status"] == "ok"


async def test_list_includes_status_error_for_failed(api: Api) -> None:
    ok_id, bad_id = await seed(api.client, [{"title": "A", "text": "a"}, {"title": "B", "text": "b"}])
    async with api.backend.uow_factory() as uow:
        await uow.documents.set_status(int(bad_id), DocumentStatus.FAILED, "embedding failed")
        await uow.commit()
    lst = {d["id"]: d for d in (await api.client.get("/kb/documents")).json()}
    assert lst[bad_id]["status"] == "failed" and lst[bad_id]["status_error"] == "embedding failed"
    assert lst[ok_id]["status_error"] is None
    spec = (await api.client.get("/openapi.json")).json()["components"]["schemas"]["DocumentListItem"]
    assert "status_error" in spec["properties"]


async def test_get_document_and_lines(api: Api) -> None:
    (doc_id,) = await seed(api.client, [{"title": "T", "text": "a1\nb2\nc3"}])
    full = (await api.client.get(f"/kb/documents/{doc_id}")).json()
    assert full["text"] == "a1\nb2\nc3" and full["lines_total"] == 3 and full["download_url"] is None
    assert full["snippets_count"] >= 1
    part = (await api.client.get(f"/kb/documents/{doc_id}?lines=2")).json()
    assert part["text"] == "a1\nb2" and part["lines_returned"] == 2
    bad = await api.client.get(f"/kb/documents/{doc_id}?lines=0")
    assert bad.status_code == 422 and bad.json()["error"] == "validation_error"


@pytest.mark.parametrize("bad", ["999", "abc", "0", "-1", "1.5", "99999999999999999999"])
async def test_get_document_404(api: Api, bad: str) -> None:
    r = await api.client.get(f"/kb/documents/{bad}")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


async def test_ids_are_strings_everywhere(api: Api) -> None:
    (doc_id,) = await seed(api.client, [{"title": "T", "text": "Ежедневный стендап проходит в 11:00."}])
    assert isinstance(doc_id, str)
    assert all(isinstance(d["id"], str) for d in (await api.client.get("/kb/documents")).json())
    assert (await api.client.get(f"/kb/documents/{doc_id}")).json()["id"] == doc_id
    ask = (await api.client.post("/kb/ask", json={"question": "Во сколько проходит стендап?"})).json()
    assert ask["sources"] and all(s["document_id"] == doc_id for s in ask["sources"])
    run = (await api.client.get("/kb/qa_runs")).json()[0]
    assert isinstance(run["id"], str) and run["sources"][0]["document_id"] == doc_id
    assert (await api.client.get(f"/kb/qa_runs/{run['id']}")).status_code == 200
    out = api.audit[-1]["output"]
    assert isinstance(out, dict) and out["sources"][0]["document_id"] == doc_id


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"title": "", "text": "x"}, "title"),
        ({"title": "   ", "text": "x"}, "title"),
        ({"title": "t" * 201, "text": "x"}, "title"),
        ({"title": "t", "text": ""}, "text"),
        ({"title": "t", "text": "x" * 100_001}, "text"),
        ({"text": "x"}, "title"),
        ({"title": "t", "text": "x", "extra": 1}, "extra"),
    ],
)
async def test_add_document_validation(api: Api, payload: dict[str, Any], field: str) -> None:
    r = await api.client.post("/kb/documents", json=payload)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"] == "validation_error"
    assert field in {e["field"] for e in body["detail"]}
    assert (await api.client.get("/kb/documents")).json() == []
    rec = api.audit[-1]
    assert rec["action"] == "POST /kb/documents" and rec["status"] == "client_error"
    assert rec["error"] == "validation_error" and rec["input"] is not None


@pytest.mark.parametrize("raw", [b"{not json", b"[1,2]", b""])
async def test_bad_json_400(api: Api, raw: bytes) -> None:
    r = await api.client.post("/kb/documents", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "bad_request"
    assert api.audit[-1]["status"] == "client_error"


async def test_ask_bad_json_400(api: Api) -> None:
    r = await api.client.post("/kb/ask", content=b"{", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json() == {"error": "bad_request", "detail": "Invalid JSON body"}


async def test_unsupported_content_type_415(api: Api) -> None:
    r = await api.client.post("/kb/documents", content=b"x", headers={"content-type": "text/plain"})
    assert r.status_code == 415 and r.json()["error"] == "unsupported_media_type"


# --- multipart upload ---------------------------------------------------------------------------


async def test_upload_md_stores_original_and_presigns(api: Api) -> None:
    data = "# Заголовок\n\nСтендап в 11:00.\n".encode()
    r = await api.client.post(
        "/kb/documents", files={"file": ("rules.md", data, "text/markdown")}, data={"title": "Правила"}
    )
    assert r.status_code == 200, r.text
    doc = (await api.client.get(f"/kb/documents/{r.json()['document_id']}")).json()
    assert (
        doc["title"] == "Правила" and doc["mime_type"] == "text/markdown" and doc["size_bytes"] == len(data)
    )
    assert doc["download_url"]
    key = doc["download_url"].split("?")[0].split("/", 3)[-1]
    assert key.endswith("rules.md")


async def test_upload_txt_title_from_filename(api: Api) -> None:
    r = await api.client.post("/kb/documents", files={"file": ("notes.txt", b"hello world", "text/plain")})
    assert r.status_code == 200
    assert (await api.client.get("/kb/documents")).json()[0]["title"] == "notes"


async def test_upload_unsupported_415(api: Api) -> None:
    r = await api.client.post(
        "/kb/documents", files={"file": ("a.exe", b"MZ\x90", "application/octet-stream")}
    )
    assert r.status_code == 415 and r.json()["error"] == "unsupported_media_type"
    inp = api.audit[-1]["input"]
    assert isinstance(inp, dict) and inp["content_type"] == "multipart/form-data"


async def test_upload_non_utf8_415(api: Api) -> None:
    r = await api.client.post(
        "/kb/documents", files={"file": ("a.txt", "привет".encode("cp1251"), "text/plain")}
    )
    assert r.status_code == 415


async def test_upload_too_large_413(make_api: Callable[..., Api]) -> None:
    api = make_api(MAX_UPLOAD_MB=1)
    r = await api.client.post(
        "/kb/documents", files={"file": ("big.txt", b"a" * (1024 * 1024 + 1), "text/plain")}
    )
    assert r.status_code == 413 and r.json()["error"] == "file_too_large"
    assert api.audit[-1]["status"] == "client_error"
    assert (await api.client.get("/kb/documents")).json() == []


async def test_upload_missing_file_422(api: Api) -> None:
    r = await api.client.post("/kb/documents", data={"title": "x"}, files={"other": ("a.txt", b"x")})
    assert r.status_code == 422 and r.json()["detail"][0]["field"] == "file"


async def test_upload_empty_file_422(api: Api) -> None:
    r = await api.client.post("/kb/documents", files={"file": ("a.txt", b"", "text/plain")})
    assert r.status_code == 422


# --- spec scenarios 2 and 3: /kb/ask ------------------------------------------------------------


async def test_answerable_questions_return_sources(
    api: Api, kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]
) -> None:
    ids = dict(zip([d["title"] for d in kb_documents], await seed(api.client, kb_documents), strict=True))
    for q in (q for q in kb_questions if not q["expected_needs_review"]):
        r = await api.client.post("/kb/ask", json={"question": q["question"]})
        assert r.status_code == 200
        body = r.json()
        assert body["needs_review"] is False, (q["question"], body)
        assert body["sources"] and body["sources"][0]["document_id"] == ids[q["expected_document"]]
        assert q["expected_quote"].lower() in " ".join(s["quote"] for s in body["sources"]).lower()
    assert api.audit[-1]["action"] == "POST /kb/ask" and api.audit[-1]["status"] == "ok"


async def test_unanswerable_needs_review_with_reason(
    api: Api, kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]
) -> None:
    await seed(api.client, kb_documents)
    q = next(q for q in kb_questions if q["expected_needs_review"])
    body = (await api.client.post("/kb/ask", json={"question": q["question"]})).json()
    assert body == {"answer": INSUFFICIENT_DATA, "sources": [], "needs_review": True}
    runs = (await api.client.get("/kb/qa_runs?needs_review=true")).json()
    assert len(runs) == 1 and runs[0]["error"] == q["expected_reason"]
    one = (await api.client.get(f"/kb/qa_runs/{runs[0]['id']}")).json()
    assert one["question"] == q["question"] and one["error"] == "no_sources"
    rec = api.audit[-1]
    assert rec["action"] == "POST /kb/ask" and rec["status"] == "needs_review"


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("error", "llm_error"),
        ("invalid_json", "llm_invalid_json"),
        ("low_confidence", "low_confidence"),
        ("hallucinate", "unverified_quote"),
    ],
)
async def test_ask_llm_problems_still_200(
    api: Api, kb_documents: list[dict[str, Any]], mode: str, reason: str
) -> None:
    await seed(api.client, kb_documents)
    api.set_chat(mode)
    r = await api.client.post("/kb/ask", json={"question": "Во сколько проходит стендап?"})
    assert r.status_code == 200 and r.json()["needs_review"] is True
    run = (await api.client.get("/kb/qa_runs?limit=1")).json()[0]
    assert run["error"] == reason


@pytest.mark.parametrize("q", ["", "ab", "x" * 2001])
async def test_ask_validation(api: Api, q: str) -> None:
    r = await api.client.post("/kb/ask", json={"question": q})
    assert r.status_code == 422 and r.json()["detail"][0]["field"] == "question"
    rec = api.audit[-1]
    assert rec["action"] == "POST /kb/ask" and rec["status"] == "client_error"
    assert (await api.client.get("/kb/qa_runs")).json() == []


# --- /ai/answer_with_sources --------------------------------------------------------------------

CTX = "Ежедневный стендап проходит в 11:00 и длится не более 15 минут."


async def test_ai_answer_strict_shape(api: Api) -> None:
    r = await api.client.post("/ai/answer_with_sources", json={"question": "Когда стендап?", "context": CTX})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"answer", "sources", "confidence", "needs_review"}
    assert body["confidence"] in ("high", "medium", "low")
    assert all(set(s) == {"quote"} for s in body["sources"])
    assert api.audit[-1]["action"] == "POST /ai/answer_with_sources" and api.audit[-1]["status"] == "ok"


@pytest.mark.parametrize(("mode", "code"), [("error", "llm_error"), ("invalid_json", "llm_invalid_json")])
async def test_ai_answer_502(api: Api, mode: str, code: str) -> None:
    api.set_chat(mode)
    r = await api.client.post("/ai/answer_with_sources", json={"question": "Когда стендап?", "context": CTX})
    assert r.status_code == 502 and r.json()["error"] == code
    errs = [a for a in api.audit if a["action"] == "POST /ai/answer_with_sources"]
    assert len(errs) == 1 and errs[0]["status"] == "error"  # one row, written by the service


async def test_ai_answer_validation(api: Api) -> None:
    r = await api.client.post("/ai/answer_with_sources", json={"question": "Когда?", "context": ""})
    assert r.status_code == 422 and r.json()["detail"][0]["field"] == "context"
    r = await api.client.post("/ai/answer_with_sources", json={"question": "Когда?", "context": "x" * 20_001})
    assert r.status_code == 422
    assert [a["status"] for a in api.audit] == ["client_error", "client_error"]


# --- /kb/qa_runs --------------------------------------------------------------------------------


async def test_qa_runs_filter_and_limit(api: Api, kb_documents: list[dict[str, Any]]) -> None:
    await seed(api.client, kb_documents)
    for q in ["Во сколько проходит стендап?", "Какая зарплата у тимлида?", "Когда запускается задача?"]:
        await api.client.post("/kb/ask", json={"question": q})
    assert len((await api.client.get("/kb/qa_runs")).json()) == 3
    assert len((await api.client.get("/kb/qa_runs?needs_review=true")).json()) == 1
    assert len((await api.client.get("/kb/qa_runs?needs_review=false")).json()) == 2
    assert len((await api.client.get("/kb/qa_runs?limit=1")).json()) == 1
    for bad in ("limit=0", "limit=101", "needs_review=maybe"):
        r = await api.client.get(f"/kb/qa_runs?{bad}")
        assert r.status_code == 422 and r.json()["error"] == "validation_error"
    assert (await api.client.get("/kb/qa_runs?limit=100")).status_code == 200


async def test_qa_run_404(api: Api) -> None:
    r = await api.client.get("/kb/qa_runs/12345")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


# --- cross-cutting ------------------------------------------------------------------------------


async def test_every_command_audited_once(api: Api) -> None:
    await api.client.post("/kb/documents", json={"title": "T", "text": "Стендап в 11:00."})
    await api.client.post("/kb/documents", json={"title": ""})
    await api.client.post("/kb/ask", json={"question": "Когда стендап?"})
    await api.client.post("/kb/ask", json={})
    await api.client.get("/kb/documents")  # queries are not audited
    assert [(a["action"], a["status"]) for a in api.audit] == [
        ("POST /kb/documents", "ok"),
        ("POST /kb/documents", "client_error"),
        ("POST /kb/ask", api.audit[2]["status"]),
        ("POST /kb/ask", "client_error"),
    ]
    assert all(isinstance(a["duration_ms"], int) for a in api.audit)


async def test_audit_input_truncated(make_api: Callable[..., Api]) -> None:
    api = make_api(AUDIT_MAX_BODY_KB=1)
    await api.client.post("/kb/documents", json={"title": "", "text": "x" * 5000})
    rec = api.audit[-1]
    assert rec["input"]["truncated"] is True  # type: ignore[index]
    assert len(rec["input"]["raw"]) <= 1024  # type: ignore[index]


async def test_cors_preflight(api: Api) -> None:
    r = await api.client.options(
        "/kb/ask",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = await api.client.options(
        "/kb/ask", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in r.headers


async def test_openapi_lists_endpoints(api: Api) -> None:
    spec = (await api.client.get("/openapi.json")).json()
    for path in [
        "/kb/documents",
        "/kb/documents/{document_id}",
        "/kb/ask",
        "/ai/answer_with_sources",
        "/kb/qa_runs",
        "/kb/qa_runs/{run_id}",
    ]:
        assert path in spec["paths"]
    assert "multipart/form-data" in spec["paths"]["/kb/documents"]["post"]["requestBody"]["content"]
    assert (await api.client.get("/docs")).status_code == 200
