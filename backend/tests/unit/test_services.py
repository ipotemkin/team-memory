"""Service-level scenarios (spec): documents listed; answerable → sources; otherwise needs_review."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.adapters.memory import MemoryAuditSink, MemoryBackend, build
from app.adapters.memory.queue import InlineTaskQueue
from app.adapters.stubs import HALLUCINATED_QUOTE, StubChatLLM, StubEmbedder, StubMode
from app.domain.errors import LLMError, LLMInvalidJSON, NotFound
from app.domain.models import INSUFFICIENT_DATA, DocumentStatus, Page, ReviewReason
from app.services.ai_answer import AiAnswerService
from app.services.ask import AskService
from app.services.documents import DocumentService
from app.services.indexing import INDEX_TASK, IndexingService


def doc_service(backend: MemoryBackend, embedder: Any = None) -> DocumentService:
    q = InlineTaskQueue()
    q.register(INDEX_TASK, IndexingService(backend.uow_factory, embedder or StubEmbedder()).index)
    return DocumentService(backend.uow_factory, queue=q)


@pytest.fixture
def backend() -> MemoryBackend:
    return build()


async def load(backend: MemoryBackend, docs: list[dict[str, Any]]) -> dict[str, int]:
    svc = doc_service(backend)
    return {d["title"]: (await svc.add(d["title"], d["text"])).id for d in docs}


def ask_svc(
    backend: MemoryBackend, mode: StubMode = "normal", extra: MemoryAuditSink | None = None
) -> AskService:
    return AskService(
        backend.uow_factory,
        backend.retriever,
        StubEmbedder(),
        StubChatLLM(mode),
        extra_audit_sinks=[extra] if extra else [],
    )


# --- scenario 1: add documents -> listed ---
async def test_documents_added_and_listed(backend: MemoryBackend, kb_documents: list[dict[str, Any]]) -> None:
    ids = await load(backend, kb_documents)
    svc = doc_service(backend)
    listed = await svc.list()
    assert {d.title for d in listed} == set(ids)
    assert all(d.status is DocumentStatus.READY for d in listed)
    assert listed[0].id == max(ids.values())  # newest first
    assert len(await svc.list(Page(limit=1, offset=1))) == 1
    snips = await svc.snippets(ids["Правила работы команды"])
    rules = next(d["text"] for d in kb_documents if d["title"] == "Правила работы команды")
    assert [s.ord for s in snips] == list(range(len([p for p in rules.split("\n\n") if p.strip()])))
    assert len(snips) >= 5  # paragraph structure chunks into several snippets
    audit = backend.store.state.audit
    assert [a.action for a in audit] == ["POST /kb/documents"] * len(kb_documents)


async def test_document_get_missing_and_validation(backend: MemoryBackend) -> None:
    svc = doc_service(backend)
    with pytest.raises(NotFound):
        await svc.get(99)
    with pytest.raises(ValidationError):
        await svc.add("x" * 201, "text")
    assert await svc.list() == []


# --- scenario 2: answerable question -> answer with verified sources ---
async def test_answerable_questions(
    backend: MemoryBackend, kb_documents: list[dict[str, Any]], kb_questions: list[dict[str, Any]]
) -> None:
    ids = await load(backend, kb_documents)
    extra = MemoryAuditSink()
    svc = ask_svc(backend, extra=extra)
    answerable = [q for q in kb_questions if not q["expected_needs_review"]]
    for q in answerable:
        res = await svc.ask(q["question"])
        assert res.needs_review is False, q["question"]
        assert res.sources and res.answer != INSUFFICIENT_DATA
        assert res.sources[0].document_id == ids[q["expected_document"]]
        assert q["expected_quote"].lower() in " ".join(s.quote for s in res.sources).lower()
    runs = backend.store.state.qa_runs.values()
    assert all(r.error is None and r.retrieval_mode == "hybrid" and r.retrieved_ids for r in runs)
    assert all(
        a.status == "ok" and a.error is None for a in backend.store.state.audit if a.action == "POST /kb/ask"
    )
    assert len(extra.records) == len(answerable)


# --- scenario 3: needs_review cases ---
async def _check_review(
    backend: MemoryBackend, svc: AskService, question: str, reason: ReviewReason, kept_sources: bool = False
) -> None:
    res = await svc.ask(question)
    assert res.needs_review is True
    assert res.answer == INSUFFICIENT_DATA
    assert bool(res.sources) is kept_sources
    run = list(backend.store.state.qa_runs.values())[-1]
    assert run.error is reason and run.needs_review
    audit = [a for a in backend.store.state.audit if a.action == "POST /kb/ask"][-1]
    assert audit.status == "needs_review" and audit.error == reason.value
    assert audit.duration_ms >= 0


async def test_unanswerable_no_sources_llm_not_called(
    backend: MemoryBackend, kb_documents: list[dict[str, Any]]
) -> None:
    await load(backend, kb_documents)
    chat = StubChatLLM()
    svc = AskService(backend.uow_factory, backend.retriever, StubEmbedder(), chat)
    await _check_review(backend, svc, "Какая зарплата у тимлида?", ReviewReason.NO_SOURCES)
    assert chat.calls == 0


async def test_empty_kb_no_sources(backend: MemoryBackend) -> None:
    await _check_review(backend, ask_svc(backend), "Во сколько стендап?", ReviewReason.NO_SOURCES)


@pytest.mark.parametrize(
    ("mode", "reason", "kept"),
    [
        ("low_confidence", ReviewReason.LOW_CONFIDENCE, False),
        ("hallucinate", ReviewReason.UNVERIFIED_QUOTE, True),
        ("invalid_json", ReviewReason.LLM_INVALID_JSON, False),
        ("error", ReviewReason.LLM_ERROR, False),
    ],
)
async def test_review_reasons(
    backend: MemoryBackend,
    kb_documents: list[dict[str, Any]],
    mode: StubMode,
    reason: ReviewReason,
    kept: bool,
) -> None:
    await load(backend, kb_documents)
    await _check_review(backend, ask_svc(backend, mode), "Во сколько проходит стендап?", reason, kept)
    if mode == "hallucinate":
        run = list(backend.store.state.qa_runs.values())[-1]
        assert HALLUCINATED_QUOTE not in [s.quote for s in run.sources]


async def test_embedder_failure_degrades_to_fts_only(
    backend: MemoryBackend, kb_documents: list[dict[str, Any]]
) -> None:
    class Broken(StubEmbedder):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("down")

    await load(backend, kb_documents)
    svc = AskService(backend.uow_factory, backend.retriever, Broken(), StubChatLLM())
    res = await svc.ask("Во сколько проходит стендап?")
    assert res.needs_review is False
    run = list(backend.store.state.qa_runs.values())[-1]
    assert run.retrieval_mode == "fts_only" and run.embedding_model is None


async def test_question_validation(backend: MemoryBackend) -> None:
    with pytest.raises(ValidationError):
        await ask_svc(backend).ask("ab")


# --- /ai/answer_with_sources ---
async def test_ai_answer_service(backend: MemoryBackend) -> None:
    svc = AiAnswerService(backend.uow_factory, StubChatLLM())
    ans = await svc.answer("Во сколько стендап?", "[S1] Ежедневный стендап проходит в 11:00.")
    assert ans.needs_review is False and ans.sources[0].quote == "Ежедневный стендап проходит в 11:00."
    assert backend.store.state.audit[-1].status == "ok"


@pytest.mark.parametrize(
    ("mode", "exc", "code"),
    [("error", LLMError, "llm_error"), ("invalid_json", LLMInvalidJSON, "llm_invalid_json")],
)
async def test_ai_answer_errors_are_audited(
    backend: MemoryBackend, mode: StubMode, exc: type[Exception], code: str
) -> None:
    svc = AiAnswerService(backend.uow_factory, StubChatLLM(mode))
    with pytest.raises(exc):
        await svc.answer("Во сколько стендап?", "Стендап в 11:00.")
    rec = backend.store.state.audit[-1]
    assert rec.status == "error" and rec.error == code


async def test_document_without_queue_stays_indexing(backend: MemoryBackend) -> None:
    doc = await DocumentService(backend.uow_factory).add("T", "Текст")
    assert doc.status == DocumentStatus.INDEXING
    st = await IndexingService(backend.uow_factory, StubEmbedder()).index(doc.id)
    assert st == DocumentStatus.READY
    assert (await DocumentService(backend.uow_factory).get(doc.id)).status == DocumentStatus.READY
    assert len(backend.store.state.embeddings) == 1


async def test_indexing_failure_marks_failed_or_retries(backend: MemoryBackend) -> None:
    class Boom:
        model_name = "boom"
        dim = 3

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise LLMError("timeout")

    doc = await DocumentService(backend.uow_factory).add("T", "Текст")
    idx = IndexingService(backend.uow_factory, Boom())
    with pytest.raises(LLMError):
        await idx.index(doc.id, final_attempt=False)  # Celery retries
    assert await idx.index(doc.id, final_attempt=True) == DocumentStatus.FAILED
    got = await DocumentService(backend.uow_factory).get(doc.id)
    assert got.status == DocumentStatus.FAILED and "timeout" in (got.status_error or "")
    assert await idx.index(10_000) == DocumentStatus.FAILED  # missing doc: no crash


async def test_enqueue_failure_marks_failed(backend: MemoryBackend) -> None:
    class DeadQueue:
        async def enqueue(self, task: str, **kwargs: Any) -> str:
            raise ConnectionError("redis down")

    doc = await DocumentService(backend.uow_factory, queue=DeadQueue()).add("T", "Текст")
    assert doc.status == DocumentStatus.FAILED and "redis down" in (doc.status_error or "")
