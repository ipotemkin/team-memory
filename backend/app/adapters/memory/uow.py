"""In-memory UnitOfWork: снимок состояния при входе, подмена при commit, иначе отбрасывание.

Так эмулируется транзакция с изоляцией «snapshot»: незафиксированные изменения никто не видит.
"""

from typing import Self

from app.adapters.memory.repositories import (
    MemoryAuditSink,
    MemoryDocumentRepository,
    MemoryQaRunRepository,
    MemorySnippetRepository,
)
from app.adapters.memory.store import MemoryState, MemoryStore
from app.domain.ports import AuditSink, DocumentRepository, QaRunRepository, SnippetRepository


class MemoryUnitOfWork:
    """Порт UnitOfWork для memory-бэкенда."""

    documents: DocumentRepository
    snippets: SnippetRepository
    qa_runs: QaRunRepository
    audit: AuditSink
    _work: MemoryState

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._bind()

    async def __aenter__(self) -> Self:
        self._bind()
        return self

    def _bind(self) -> None:
        """Берёт свежую копию зафиксированного состояния и создаёт репозитории поверх неё."""
        self._work = self._store.state.clone()
        self.documents = MemoryDocumentRepository(self._work)
        self.snippets = MemorySnippetRepository(self._work)
        self.qa_runs = MemoryQaRunRepository(self._work)
        self.audit = MemoryAuditSink(self._work)

    async def __aexit__(self, *exc: object) -> None:
        self._work = self._store.state  # всё незафиксированное отбрасывается

    async def commit(self) -> None:
        """Публикует рабочую копию как новое зафиксированное состояние и начинает новую копию."""
        self._store.state = self._work
        self._bind()
