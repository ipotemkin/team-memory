"""Гибридный поиск на Postgres + pgvector: FTS ∪ вектор (активная модель, ready-документы), RRF, score."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from app.adapters.memory.queue import InlineTaskQueue
from app.adapters.postgres import PostgresBackend
from app.adapters.postgres.admin import bootstrap_embedding_model
from app.adapters.postgres.retriever import rrf_merge
from app.adapters.stubs import StubChatLLM, StubEmbedder
from app.domain.models import DocumentStatus, ScoredSnippet
from app.services.ask import AskService
from app.services.documents import DocumentService
from app.services.indexing import INDEX_TASK, IndexingService
from tests.support import migrate_once, needs_pg, pg_backend

pytestmark = [pytest.mark.integration, needs_pg]
EMB = StubEmbedder()


@pytest.fixture
async def pg() -> AsyncIterator[PostgresBackend]:
    migrate_once()
    async for b in pg_backend():
        assert isinstance(b, PostgresBackend)
        await bootstrap_embedding_model(b.engine, EMB.model_name, "stub", EMB.dim)
        yield b


def docs(pg: PostgresBackend) -> DocumentService:
    q = InlineTaskQueue()
    q.register(INDEX_TASK, IndexingService(pg.uow_factory, EMB).index)
    return DocumentService(pg.uow_factory, queue=q)


async def test_bootstrap_activates_model_and_creates_index(pg: PostgresBackend) -> None:
    async with pg.engine.connect() as conn:
        st = (
            await conn.execute(
                text("SELECT status::text FROM app.embedding_models WHERE name = :n"), {"n": EMB.model_name}
            )
        ).scalar_one()
        idx = (
            (
                await conn.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE indexname LIKE 'ix_snip_emb_hnsw_%'")
                )
            )
            .scalars()
            .all()
        )
    assert st == "active"
    assert any("vector(64)" in d and EMB.model_name in d for d in idx)
    # idempotent; a second model does not steal 'active'
    assert await bootstrap_embedding_model(pg.engine, EMB.model_name, "stub", 64) == EMB.model_name
    assert await bootstrap_embedding_model(pg.engine, "other:model", "stub", 8) == EMB.model_name


async def test_indexing_writes_embeddings_and_ready(pg: PostgresBackend) -> None:
    d = await docs(pg).add("Правила", "Стендап в 11:00.\n\nОтпуск согласуется с руководителем.")
    assert d.status == DocumentStatus.READY and d.indexed_at is not None
    async with pg.engine.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM app.snippet_embeddings"))).scalar_one()
    assert n == 2


async def test_vector_channel_finds_without_fts_overlap(pg: PostgresBackend) -> None:
    d = await docs(pg).add("Правила", "Ежедневный стендап проходит в 11:00.")
    target = "Ежедневный стендап проходит в 11:00."
    qvec = (await EMB.embed([target]))[0]
    # query text with no lexemes in common: FTS finds nothing, vector channel finds the snippet
    res = await pg.retriever.search("zzzz qqqq", qvec, EMB.model_name, 5)
    assert res and res[0].document_id == d.id and res[0].score > 0.99
    # without qvec (embedder down → fts_only) there is nothing
    assert await pg.retriever.search("zzzz qqqq", None, None, 5) == []
    # inactive / unknown model → vector channel disabled
    assert await pg.retriever.search("zzzz qqqq", qvec, "other:model", 5) == []


async def test_vector_channel_respects_ready_status(pg: PostgresBackend) -> None:
    d = await docs(pg).add("Правила", "Ежедневный стендап проходит в 11:00.")
    async with pg.uow_factory() as uow:
        await uow.documents.set_status(d.id, DocumentStatus.FAILED, "x")
        await uow.commit()
    qvec = (await EMB.embed(["Ежедневный стендап проходит в 11:00."]))[0]
    assert await pg.retriever.search("стендап", qvec, EMB.model_name, 5) == []


async def test_hybrid_ask_on_postgres(pg: PostgresBackend) -> None:
    await docs(pg).add(
        "Правила", "Ежедневный стендап проходит в 11:00.\n\nОтпуск согласуется с руководителем."
    )
    svc = AskService(pg.uow_factory, pg.retriever, EMB, StubChatLLM(), min_score=0.6)
    res = await svc.ask("Во сколько проходит стендап?")
    assert res.needs_review is False and "11:00" in res.sources[0].quote
    async with pg.engine.connect() as conn:
        mode = (
            await conn.execute(text("SELECT retrieval_mode FROM app.qa_runs ORDER BY id DESC LIMIT 1"))
        ).scalar_one()
    assert mode == "hybrid"


def test_rrf_merge_orders_by_fused_rank() -> None:
    def s(i: int, sc: float) -> ScoredSnippet:
        return ScoredSnippet(snippet_id=i, document_id=1, text=str(i), score=sc)

    fts = [s(1, 0.9), s(2, 0.5)]
    vec = [s(2, 0.8), s(3, 0.7)]
    out = rrf_merge(fts, vec, 3)
    assert [x.snippet_id for x in out] == [2, 1, 3]  # 2 is in both lists
    assert out[0].score == 0.8  # normalized score = best channel score
