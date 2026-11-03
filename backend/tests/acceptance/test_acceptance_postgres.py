"""Acceptance: the 3 spec scenarios through HTTP on the real Postgres backend (+ S3 for uploads).

Runs only with TEST_DATABASE_URL (S3 upload case also needs TEST_S3_ENDPOINT_URL).
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.adapters.memory.queue import InlineTaskQueue
from app.adapters.stubs import StubChatLLM, StubEmbedder
from app.composition import Container
from app.config import Settings
from app.domain.models import INSUFFICIENT_DATA
from app.main import create_app
from app.services.health import HealthService
from tests.support import TEST_DB_URL, migrate_once, needs_pg, needs_s3, pg_backend, s3_storage

pytestmark = [pytest.mark.integration, needs_pg]


async def _client(storage: Any) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    migrate_once()
    async for b in pg_backend():
        c = Container(
            uow_factory=b.uow_factory,  # type: ignore[attr-defined]
            retriever=b.retriever,  # type: ignore[attr-defined]
            storage=storage,
            embedder=StubEmbedder(),
            chat=StubChatLLM(),
            queue=InlineTaskQueue(),
            extra_audit_sinks=[],
            settings=Settings(_env_file=None, DB_URL=str(TEST_DB_URL)),  # type: ignore[call-arg]
        )
        app = create_app(c.settings, c, HealthService([]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as cl:
            yield cl, b


@pytest.fixture
async def pg() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    from app.adapters.memory import MemoryFileStorage

    async for x in _client(MemoryFileStorage()):
        yield x


async def _audit(b: Any) -> list[tuple[str, str, str | None]]:
    async with b.engine.connect() as conn:
        rows = await conn.execute(text("SELECT action, status, error FROM app.audit_runs ORDER BY id"))
        return [tuple(r) for r in rows]


async def _seed(cl: httpx.AsyncClient, docs: list[dict[str, Any]]) -> dict[str, int]:
    out = {}
    for d in docs:
        r = await cl.post("/kb/documents", json={"title": d["title"], "text": d["text"]})
        assert r.status_code == 200, r.text
        out[d["title"]] = r.json()["document_id"]
    return out


async def test_scenario1_add_and_list(
    pg: tuple[httpx.AsyncClient, Any], kb_documents: list[dict[str, Any]]
) -> None:
    cl, b = pg
    ids = await _seed(cl, kb_documents)
    lst = (await cl.get("/kb/documents")).json()
    assert {d["id"] for d in lst} == set(ids.values()) and {d["status"] for d in lst} == {"ready"}
    doc = (await cl.get(f"/kb/documents/{ids['Правила работы команды']}?lines=1")).json()
    assert doc["lines_returned"] == 1 and doc["snippets_count"] >= 1
    assert [a[:2] for a in await _audit(b)] == [("POST /kb/documents", "ok")] * len(kb_documents)


async def test_scenario2_answerable(
    pg: tuple[httpx.AsyncClient, Any], kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]
) -> None:
    cl, _ = pg
    ids = await _seed(cl, kb_documents)
    for q in (q for q in kb_questions if not q["expected_needs_review"]):
        body = (await cl.post("/kb/ask", json={"question": q["question"]})).json()
        assert body["needs_review"] is False, (q["question"], body)
        assert body["sources"][0]["document_id"] == ids[q["expected_document"]]
        assert q["expected_quote"].lower() in " ".join(s["quote"] for s in body["sources"]).lower()


async def test_scenario3_unanswerable(
    pg: tuple[httpx.AsyncClient, Any], kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]
) -> None:
    cl, b = pg
    await _seed(cl, kb_documents)
    q = next(q for q in kb_questions if q["expected_needs_review"])
    body = (await cl.post("/kb/ask", json={"question": q["question"]})).json()
    assert body == {"answer": INSUFFICIENT_DATA, "sources": [], "needs_review": True}
    runs = (await cl.get("/kb/qa_runs?needs_review=true")).json()
    assert [r["error"] for r in runs] == ["no_sources"]
    assert (await cl.get(f"/kb/qa_runs/{runs[0]['id']}")).json()["error"] == "no_sources"
    assert (await _audit(b))[-1][:2] == ("POST /kb/ask", "needs_review")


async def test_validation_failure_is_audited(pg: tuple[httpx.AsyncClient, Any]) -> None:
    cl, b = pg
    r = await cl.post("/kb/documents", json={"title": "", "text": "x"})
    assert r.status_code == 422
    assert await _audit(b) == [("POST /kb/documents", "client_error", "validation_error")]
    assert (await cl.get("/kb/documents")).json() == []


@needs_s3
async def test_upload_to_s3_and_presign() -> None:
    async for st in s3_storage():
        async for cl, _ in _client(st):
            data = "Стендап в 11:00.\n".encode()
            r = await cl.post("/kb/documents", files={"file": ("rules.md", data, "text/markdown")})
            assert r.status_code == 200, r.text
            doc = (await cl.get(f"/kb/documents/{r.json()['document_id']}")).json()
            assert doc["download_url"] and doc["size_bytes"] == len(data)
            async with httpx.AsyncClient() as raw:
                got = await raw.get(doc["download_url"])
            assert got.status_code == 200 and got.content == data
