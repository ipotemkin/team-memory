"""Postgres-only behaviour: DB CHECK invariants, partitions, HNSW helper, UoW atomicity, service flow."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.adapters.memory.queue import InlineTaskQueue
from app.adapters.postgres import PostgresBackend
from app.adapters.postgres.admin import bootstrap_embedding_model, create_hnsw_index, ensure_audit_partitions
from app.adapters.stubs import StubChatLLM, StubEmbedder
from app.domain.models import AuditRecord, Page, QaRun, ReviewReason
from app.services.ask import AskService
from app.services.documents import DocumentService
from app.services.indexing import INDEX_TASK, IndexingService
from tests.contract.helpers import new_doc, snip
from tests.support import migrate_once, needs_pg, pg_backend

pytestmark = [pytest.mark.integration, needs_pg]


@pytest.fixture
async def pg() -> AsyncIterator[PostgresBackend]:
    migrate_once()
    async for b in pg_backend():
        x: Any = b
        yield x


async def test_check_reviewed_has_sources(pg: PostgresBackend) -> None:
    with pytest.raises(IntegrityError):
        async with pg.uow_factory() as uow:
            await uow.qa_runs.add(QaRun(question="q", answer="a", needs_review=False))
            await uow.commit()


async def test_check_review_has_reason(pg: PostgresBackend) -> None:
    with pytest.raises(IntegrityError):
        async with pg.uow_factory() as uow:
            await uow.qa_runs.add(QaRun(question="q", answer="a", needs_review=True))
            await uow.commit()


async def test_text_length_check(pg: PostgresBackend) -> None:
    async with pg.engine.connect() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(
                text(
                    "INSERT INTO app.documents (title, text, content_sha256) "
                    "VALUES ('t', :x, repeat('0', 64))"
                ),
                {"x": "x" * 100_001},
            )


async def test_uow_rolls_back_qa_run_and_audit_together(pg: PostgresBackend) -> None:
    with pytest.raises(RuntimeError):
        async with pg.uow_factory() as uow:
            await uow.qa_runs.add(
                QaRun(question="q", answer="a", needs_review=True, error=ReviewReason.NO_SOURCES)
            )
            await uow.audit.record(AuditRecord(action="POST /kb/ask", status="needs_review", duration_ms=1))
            raise RuntimeError("boom")
    async with pg.engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT (SELECT count(*) FROM app.qa_runs) + (SELECT count(*) FROM app.audit_runs)")
            )
        ).scalar_one()
    assert n == 0


async def test_audit_partitions_and_routing(pg: PostgresBackend) -> None:
    await ensure_audit_partitions(pg.engine, 3)  # creates up to month +3 (may already exist)
    assert await ensure_audit_partitions(pg.engine, 3) == 0  # idempotent
    async with pg.engine.connect() as conn:
        name = (
            await conn.execute(
                text(
                    "SELECT 'app.audit_runs_' || "
                    "to_char(date_trunc('month', now()) + interval '3 months', 'YYYY_MM')"
                )
            )
        ).scalar_one()
        assert (await conn.execute(text("SELECT to_regclass(:n)"), {"n": name})).scalar_one() is not None
    async with pg.uow_factory() as uow:
        await uow.audit.record(AuditRecord(action="x", status="ok", duration_ms=0))
        await uow.commit()
    async with pg.engine.connect() as conn:
        part = (await conn.execute(text("SELECT tableoid::regclass::text FROM app.audit_runs"))).scalar_one()
    assert part.startswith("app.audit_runs_20")


async def test_hnsw_index_helper(pg: PostgresBackend) -> None:
    async with pg.uow_factory() as uow:
        d = await uow.documents.add(new_doc())
        [s] = await uow.snippets.replace_for_document(d.id, [snip(d.id, 0, "текст")])
        await uow.snippets.upsert_embeddings(
            "stub:hash-4", [(s.id or 0, s.text_sha256, [0.1, 0.2, 0.3, 0.4])]
        )
        await uow.commit()
    ddl = await create_hnsw_index(pg.engine, "stub:hash-4")
    assert "vector(4)" in ddl and "hnsw" in ddl
    async with pg.engine.connect() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM pg_indexes WHERE indexdef LIKE '%stub:hash-4%'"))
        ).scalar_one()
    assert n == 1


async def test_service_flow_on_postgres(
    pg: PostgresBackend, kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]
) -> None:
    q_ = InlineTaskQueue()
    q_.register(INDEX_TASK, IndexingService(pg.uow_factory, StubEmbedder()).index)
    await bootstrap_embedding_model(pg.engine, "stub:hash-64", "stub", 64)
    docs = DocumentService(pg.uow_factory, queue=q_)
    ids = {d["title"]: (await docs.add(d["title"], d["text"])).id for d in kb_documents}
    assert len(await docs.list()) == len(kb_documents)
    svc = AskService(pg.uow_factory, pg.retriever, StubEmbedder(), StubChatLLM())
    for q in kb_questions:
        res = await svc.ask(q["question"])
        if not q["expected_needs_review"]:
            assert res.needs_review is False, q["question"]
            assert res.sources[0].document_id == ids[q["expected_document"]]
        else:
            assert res.needs_review is True and res.sources == []
    async with pg.uow_factory() as uow:
        runs = await uow.qa_runs.list(Page(), needs_review=True)
    assert [r.error for r in runs] == [ReviewReason.NO_SOURCES] * sum(
        q["expected_needs_review"] for q in kb_questions
    )
    async with pg.engine.connect() as conn:
        n = (
            await conn.execute(text("SELECT count(*) FROM app.audit_runs WHERE action = 'POST /kb/ask'"))
        ).scalar_one()
    assert n == len(kb_questions)
