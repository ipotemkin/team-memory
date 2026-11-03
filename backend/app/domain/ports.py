"""Ports (typing.Protocol). Services depend only on these and domain models."""

from __future__ import annotations

from typing import Any, Protocol, Self

from app.domain.models import (
    AiAnswer,
    AuditRecord,
    Document,
    DocumentStatus,
    EmbeddingModelInfo,
    NewDocument,
    Page,
    QaRun,
    ScoredSnippet,
    Snippet,
    StoredObject,
)


class DocumentRepository(Protocol):
    async def add(self, doc: NewDocument) -> Document: ...
    async def get(self, id: int) -> Document | None: ...
    async def list(self, page: Page) -> list[Document]: ...
    async def set_status(self, id: int, status: DocumentStatus, error: str | None = None) -> None: ...


class SnippetRepository(Protocol):
    async def replace_for_document(self, document_id: int, snippets: list[Snippet]) -> list[Snippet]: ...
    async def list_for_document(self, document_id: int) -> list[Snippet]: ...
    async def upsert_embeddings(self, model: str, items: list[tuple[int, str, list[float]]]) -> int: ...
    async def missing_embeddings(self, model: str, batch: int) -> list[Snippet]: ...


class Retriever(Protocol):
    async def search(
        self, query: str, qvec: list[float] | None, model: str | None, k: int
    ) -> list[ScoredSnippet]: ...


class QaRunRepository(Protocol):
    async def add(self, run: QaRun) -> QaRun: ...
    async def get(self, id: int) -> QaRun | None: ...
    async def list(self, page: Page, needs_review: bool | None = None) -> list[QaRun]: ...


class AuditSink(Protocol):
    async def record(self, rec: AuditRecord) -> None: ...


class FileStorage(Protocol):
    async def put(self, key: str, data: bytes, mime: str) -> StoredObject: ...
    async def get(self, key: str) -> bytes: ...
    async def presign(self, key: str, ttl_s: int = 900) -> str: ...
    async def delete(self, key: str) -> None: ...


class Embedder(Protocol):
    model_name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatLLM(Protocol):
    model_name: str

    async def answer_with_sources(self, question: str, context: str) -> AiAnswer:
        """Return a validated AiAnswer; raise LLMError / LLMInvalidJSON."""
        ...


class TaskQueue(Protocol):
    async def enqueue(self, task: str, **kwargs: Any) -> str: ...


class UnitOfWork(Protocol):
    documents: DocumentRepository
    snippets: SnippetRepository
    qa_runs: QaRunRepository
    audit: AuditSink  # transactional sink (Postgres) or memory

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc: object) -> None: ...  # rollback unless committed
    async def commit(self) -> None: ...


class EmbeddingModelRegistry(Protocol):
    """Registry of embedding models + per-model embedding data (blue/green reindex)."""

    async def list(self) -> list[EmbeddingModelInfo]: ...
    async def get(self, name: str) -> EmbeddingModelInfo | None: ...
    async def active(self) -> EmbeddingModelInfo | None: ...
    async def register(self, name: str, provider: str, dim: int) -> EmbeddingModelInfo:
        """Raise Conflict if the name is already registered."""
        ...

    async def bootstrap(self, name: str, provider: str, dim: int) -> str:
        """Register if missing; activate if no model is active. Returns the active model name."""
        ...

    async def set_status(self, name: str, status: str, error: str | None = None) -> None:
        """Set status and error together (error=None clears a previous failure)."""
        ...

    async def set_progress(self, name: str, done: int, total: int) -> None: ...
    async def coverage(self, name: str) -> tuple[int, int]:
        """(snippets with an up-to-date embedding of this model, total snippets)."""
        ...

    async def activate(self, name: str) -> None:
        """Atomically retire the current active model and make ``name`` active."""
        ...

    async def delete(self, name: str) -> int:
        """Delete the model's embeddings, index and registry row; returns deleted embeddings."""
        ...

    async def ensure_index(self, name: str) -> None: ...
